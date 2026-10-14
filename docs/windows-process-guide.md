# Windows model process ownership

[All guides](README.md) · [Project overview](../README.md)

Run Windows model workloads through the application worker so training, inference, model validation and semantic subprocesses share the GPU/CPU capacity lease. The launcher assigns workloads to a Windows Job Object before model imports and refuses work when assignment fails. Standalone CLI commands use a separate process-ownership path.

The parent owns the durable job handle. The child closes its temporary joining handle immediately. Kill-on-last-handle-close covers job members and ordinary `CreateProcess` descendants; this implementation enables neither breakaway option. Windows job assignment can conflict with an existing host job policy, so qualify the actual service host and preserve refusal. [Microsoft job-object semantics](https://learn.microsoft.com/en-us/windows/win32/procthread/job-objects).

## Capacity and shutdown

Windows byte-range locks do not provide Linux's inherited open-file-description behavior. Admission therefore takes the file lock and checks the bounded prior-job marker and active-process accounting before granting capacity. Invalid metadata or unavailable accounting refuses admission. An observer that retains a job handle may delay termination; positive membership still withholds capacity after parent death. [Microsoft file-lock inheritance](https://learn.microsoft.com/en-us/windows/win32/api/fileapi/nf-fileapi-lockfileex).

Normal shutdown waits for direct-child exit, closes and waits for the owned job, then releases capacity. The resident broker stops owned descendants before joining its output reader or closing pipes. Preserve this order: a descendant can retain a pipe handle after its direct parent exits. The marker is internal coordination metadata; do not edit it to force capacity while a model job may still be active.

Do not use exit status alone to confirm training completion: a forced Windows Job Object termination can report zero. Require a valid result, complete manifest, current job ownership and independent model checks. Virtual-environment launchers may add processes to the job, so inspect membership rather than assuming a fixed process count.

## Verification boundaries

Run the maintained native tests from the repository's platform environment:

```powershell
python -m unittest discover -s tests -p test_windows_job.py -v
python -m unittest discover -s tests -v
```

Six actual Windows OS tests cover normal descendant cleanup, parent crash, death before joining, unavailable job/malformed marker refusal, retained-handle capacity fencing and launch preconditions. They use CPU sleepers and completion markers; they do not exercise model training or establish domain quality. Linux skips these six tests. Windows skips seven Linux kernel/FD/thread cases. Report applicable counts explicitly rather than presenting skipped cases as executed.

Validate runtime crashes, training-worker crashes and durable recovery separately on the target host. Other Windows versions, service identities, nested host jobs, network filesystems, power loss, broad load and provider subprocesses need their own checks. Hosted HTTP and cloud transport subprocesses do not inherit this model-launch contract.
