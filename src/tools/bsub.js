// Generic LSF (bsub) tools: submit a worker script, poll the job, fetch artifacts.
// Nothing in this file names a simulator — a Spectre run is just a worker choice.
import fs from 'node:fs'
import path from 'node:path'
import { SCRIPTS_DIR, readJsonIfExists, resolveDir } from '../py.js'
import { downloadFile, execRemote, resolveTarget, shq, uploadFile } from '../remote.js'
import {
  DEFAULT_INCLUDES, buildSubmitCommand, collectRunFiles, countFetched,
  defaultRemotePython, defaultRemoteScriptsDir, extractArchive, fetchCommand,
  listCommand, parseJobId, parsePollOutput, parseRemoteList, pluginScripts,
  pollCommand, postSubmitCommand, readJob, readWorkspaceConfig, remoteJoin,
  resolveRemoteDir, sanitizeRel, templateUploadPlan, writeJob,
} from '../bsub.js'

function totalFromConfig(runDir) {
  const cfg = readJsonIfExists(path.join(runDir, 'sweep_config.json'))
  const params = cfg?.parameters
  if (!params || typeof params !== 'object') return null
  const lists = Object.values(params).filter(Array.isArray)
  if (!lists.length) return null
  if (cfg.sweep_mode === 'zip') return lists[0]?.length ?? null
  return lists.reduce((n, l) => n * l.length, 1)
}

function underRoot(root, candidate) {
  return candidate === root || candidate.startsWith(root + '/')
}

export function bsubTools(ctx) {
  return [
    {
      name: 'bsub_submit',
      description: 'Submit a remote LSF batch through dsh-remote: uploads the run directory (template + '
        + 'sweep_config.json + metadata) and the plugin worker scripts, then runs the chosen worker under bsub. '
        + 'The worker path is plugin-relative (e.g. "spectre/spectre_sweep_worker.py") or absolute on the remote; '
        + 'worker_args are passed through. Writes .bsub_job.json in the run directory for bsub_poll/bsub_fetch. '
        + 'Requires a live session in the target machine\'s remote project (or a caller session already inside it).',
      parameters: {
        run_dir: { type: 'string', required: true, description: 'Local run directory (holds template and sweep_config.json)' },
        remote_dir: { type: 'string', required: true, description: 'Remote batch directory: absolute, or relative to the mirror remotePath' },
        worker: { type: 'string', required: true, description: 'Plugin-relative worker script (e.g. spectre/spectre_sweep_worker.py) or an absolute remote path' },
        worker_args: { type: 'array', items: { type: 'string' }, description: 'Arguments passed to the worker (e.g. --config sweep_config.json --max-parallel 8)' },
        machine_id: { type: 'string', description: 'dsh-remote machine id/name (default: the calling session must already be inside the mirror)' },
        remote_python: { type: 'string', description: 'Remote Python executable (default: workspace remote_python or python3; use the full path on clusters)' },
        remote_scripts_dir: { type: 'string', description: 'Remote scripts root (default: <remotePath>/.harnessams/scripts or workspace remote_scripts_dir)' },
        files: { type: 'array', items: { type: 'string' }, description: 'Run-dir files to upload (default: top-level regular files)' },
        queue: { type: 'string', description: 'LSF queue (default normal)' },
        job_name: { type: 'string', description: 'bsub -J name (default hams-<run dir name>)' },
        sync_scripts: { type: 'boolean', description: 'Upload scripts/ to the remote scripts root (default true; pass false after a successful sync)' },
        overwrite: { type: 'boolean', description: 'Allow submitting into a remote directory that already has point_meta.json files' },
      },
      async execute(args, exec) {
        const runDir = resolveDir(args.run_dir)
        const target = await resolveTarget(ctx, exec, args.machine_id)
        const remoteDir = resolveRemoteDir(target.remoteRoot, args.remote_dir)
        const wsConfig = readWorkspaceConfig().config
        const syncScripts = args.sync_scripts !== false
        const rawScripts = args.remote_scripts_dir || wsConfig.remote_scripts_dir
          || defaultRemoteScriptsDir(target.remoteRoot)
        const scriptsRoot = rawScripts.startsWith('/') ? rawScripts.replace(/\/+$/, '')
          : resolveRemoteDir(target.remoteRoot, rawScripts)
        if (syncScripts && !underRoot(target.remoteRoot, scriptsRoot)) {
          throw new Error(`remote_scripts_dir must be under the remote workspace when syncing scripts: ${scriptsRoot}`)
        }

        let remoteWorker
        let workerRel = null
        const workerRaw = String(args.worker)
        if (workerRaw.startsWith('/') || /^[A-Za-z]:[\\/]/.test(workerRaw)) {
          remoteWorker = workerRaw.replace(/\\/g, '/')
        } else {
          workerRel = sanitizeRel(workerRaw)
          const localWorker = path.join(SCRIPTS_DIR, ...workerRel.split('/'))
          if (!fs.existsSync(localWorker)) {
            throw new Error(`worker script not found in the plugin: ${workerRel}`)
          }
          remoteWorker = remoteJoin(scriptsRoot, workerRel)
        }

        const probe = await execRemote(ctx, target.agent,
          `ls -1 ${shq(remoteDir)}/*/point_meta.json 2>/dev/null | wc -l`, { timeoutMs: 30_000 })
        const existing = Number(String(probe.stdout ?? '').trim()) || 0
        if (existing > 0 && !args.overwrite) {
          throw new Error(`remote_dir already holds ${existing} point_meta.json file(s): ${remoteDir} `
            + '(pass overwrite=true only if replacing that batch is intended)')
        }

        const sweepConfig = readJsonIfExists(path.join(runDir, 'sweep_config.json'))
        const files = templateUploadPlan(runDir,
          collectRunFiles(runDir, args.files), sweepConfig)

        if (syncScripts) {
          for (const script of pluginScripts()) {
            const remote = remoteJoin(scriptsRoot, script.rel)
            const up = await uploadFile(ctx, target.agent, script.local, remote)
            if (!up.ok) throw new Error(`upload failed for ${script.rel}: ${up.text}`)
          }
        }

        for (const file of files) {
          const name = file.uploadName || file.name
          const up = await uploadFile(ctx, target.agent, file.local, remoteJoin(remoteDir, name))
          if (!up.ok) throw new Error(`upload failed for ${name}: ${up.text}`)
        }

        const remotePython = args.remote_python || wsConfig.remote_python || defaultRemotePython()
        const queue = args.queue || 'normal'
        const jobName = args.job_name || `hams-${path.basename(runDir)}`
        const command = buildSubmitCommand({
          remoteDir,
          queue,
          jobName,
          python: remotePython,
          workerPath: remoteWorker,
          workerArgs: args.worker_args || [],
        })
        const submitted = await execRemote(ctx, target.agent, command, { timeoutMs: 90_000 })
        if (!submitted.ok) {
          throw new Error(`bsub submission failed (exit ${submitted.exitCode}): `
            + `${(submitted.stderr || submitted.stdout || '').slice(0, 1200)}`)
        }
        const jobId = parseJobId(submitted.stdout)
        if (!jobId) {
          throw new Error('bsub did not report a job id: ' + (submitted.stdout || '').slice(0, 800))
        }

        const warnings = []
        const post = await execRemote(ctx, target.agent, postSubmitCommand(remoteDir, jobId),
          { timeoutMs: 30_000 })
        if (!post.ok) warnings.push(`post-submit verification failed: ${(post.stderr || post.text).slice(0, 300)}`)
        if (post.stdout.includes('__ERRLOG__')) {
          warnings.push('remote errlog.txt exists — the worker failed to start (use bsub_poll for details)')
        }
        if (!post.stdout.includes('__STARTED__')) {
          warnings.push('no .worker_started marker yet — the job is queued or the worker has not run')
        }

        const job = {
          machine_id: args.machine_id || target.machine?.id || null,
          machine_name: target.machine?.name || null,
          remote_dir: remoteDir,
          scripts_dir: scriptsRoot,
          worker: remoteWorker,
          worker_args: args.worker_args || [],
          job_id: jobId,
          queue,
          remote_python: remotePython,
          scheduler: 'lsf',
          submitted_at: new Date().toISOString(),
          total_points: totalFromConfig(runDir),
        }
        writeJob(runDir, job)
        return {
          ok: true,
          job_id: jobId,
          state: post.stdout.includes('__STARTED__') ? 'running' : 'submitted',
          remote_dir: remoteDir,
          scripts_dir: scriptsRoot,
          warnings,
          job_file: path.join(runDir, '.bsub_job.json'),
          output: (submitted.stdout || '').trim().slice(0, 2000),
        }
      },
    },
    {
      name: 'bsub_poll',
      description: 'Poll a remote LSF batch submitted by bsub_submit: bjobs state plus the batch\'s own progress '
        + '(point_meta.json count, sweep_progress.json summary, errlog tail). The machine, directory and job id '
        + 'default to <run_dir>/.bsub_job.json. A finished job that left the queue is reported as completed when '
        + 'all point_meta.json files exist.',
      parameters: {
        run_dir: { type: 'string', required: true, description: 'Local run directory holding .bsub_job.json' },
        machine_id: { type: 'string', description: 'Override the machine id/name from the job file' },
        remote_dir: { type: 'string', description: 'Override the remote batch directory' },
        job_id: { type: 'string', description: 'Override the LSF job id' },
        scheduler: { type: 'string', enum: ['lsf', 'none'], description: 'lsf (default) checks bjobs; none relies on the batch progress files' },
      },
      async execute(args, exec) {
        const runDir = resolveDir(args.run_dir)
        const job = readJob(runDir)
        const machineId = args.machine_id || job?.machine_id || undefined
        const target = await resolveTarget(ctx, exec, machineId)
        const remoteDir = resolveRemoteDir(target.remoteRoot, args.remote_dir || job?.remote_dir)
        const jobId = args.job_id || job?.job_id
        if (!jobId) throw new Error('no job id: pass job_id or run bsub_submit first')
        const scheduler = args.scheduler || job?.scheduler || 'lsf'
        const r = await execRemote(ctx, target.agent,
          pollCommand({ remoteDir, jobId, scheduler }), { timeoutMs: 30_000 })
        if (!r.ok) throw new Error(`poll failed: ${(r.stderr || r.stdout || r.text).slice(0, 800)}`)
        const parsed = parsePollOutput(r.stdout, { total: job?.total_points })
        return {
          ok: true,
          job_id: jobId,
          remote_dir: remoteDir,
          scheduler,
          ...parsed,
          output: (r.stdout || '').trim().slice(0, 1500),
        }
      },
    },
    {
      name: 'bsub_fetch',
      description: 'Fetch artifacts from a remote LSF batch: tar-gzips the requested files on the remote host, '
        + 'downloads the archive over dsh-remote and extracts it into the local run directory (the archive is '
        + 'kept as fetch_<job_id>.tgz). If tar is unavailable it falls back to a bounded per-file download. '
        + 'Afterwards sim_collect / spectre_parse / snp_read can read the local run directory directly.',
      parameters: {
        run_dir: { type: 'string', required: true, description: 'Local run directory to extract into' },
        includes: { type: 'array', items: { type: 'string' },
          description: 'Files to fetch: names (point_meta.json) or paths (*/netlist.scs.raw/sp.sp); '
            + 'default point_meta.json, sweep_progress.json, errlog.txt, bsub.out, bsub.err' },
        machine_id: { type: 'string', description: 'Override the machine id/name from the job file' },
        remote_dir: { type: 'string', description: 'Override the remote batch directory' },
        job_id: { type: 'string', description: 'Override the LSF job id (also names the local archive)' },
      },
      async execute(args, exec) {
        const runDir = resolveDir(args.run_dir)
        const job = readJob(runDir)
        const machineId = args.machine_id || job?.machine_id || undefined
        const target = await resolveTarget(ctx, exec, machineId)
        const remoteDir = resolveRemoteDir(target.remoteRoot, args.remote_dir || job?.remote_dir)
        const jobId = args.job_id || job?.job_id || 'manual'
        const includes = Array.isArray(args.includes) && args.includes.length
          ? args.includes.map(String) : DEFAULT_INCLUDES

        const packed = await execRemote(ctx, target.agent,
          fetchCommand({ remoteDir, includes }), { timeoutMs: 120_000 })
        if (!packed.ok) {
          throw new Error(`remote packing failed: ${(packed.stderr || packed.stdout || packed.text).slice(0, 800)}`)
        }
        const archiveRemote = remoteJoin(remoteDir, '.bsub_fetch.tgz')
        const archiveLocal = path.join(runDir, `fetch_${jobId}.tgz`)
        const dl = await downloadFile(ctx, target.agent, archiveRemote, archiveLocal)
        if (!dl.ok) throw new Error(`archive download failed: ${dl.text.slice(0, 800)}`)

        let mode = 'archive'
        try {
          await extractArchive(archiveLocal, runDir)
        } catch (e) {
          mode = 'per-file fallback'
          const listed = await execRemote(ctx, target.agent,
            listCommand({ remoteDir, includes }), { timeoutMs: 60_000 })
          const rels = parseRemoteList(listed.stdout)
          if (!rels.length) {
            throw new Error(`archive extraction failed (${e.message}) and the remote listed no files`)
          }
          for (const rel of rels) {
            const safe = sanitizeRel(rel)
            const local = path.join(runDir, ...safe.split('/'))
            const one = await downloadFile(ctx, target.agent, remoteJoin(remoteDir, safe), local)
            if (!one.ok) throw new Error(`per-file download failed for ${safe}: ${one.text}`)
          }
        }
        const fetched = countFetched(runDir)
        return {
          ok: true,
          job_id: jobId,
          remote_dir: remoteDir,
          archive: archiveLocal,
          mode,
          includes,
          fetched,
        }
      },
    },
  ]
}
