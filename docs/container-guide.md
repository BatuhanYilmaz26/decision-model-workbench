# Run and verify the local container platform

Run the API, independent worker and browser with a Linux/amd64 CPU image using CPython 3.12.15. Compose keeps workspace data in a named volume. The verifier checks CPU training, release, inference, service replacement and recovery using fictional examples. For GPU training and serving, use a separate [model image](model-container-guide.md) or [model environment](setup.md#configure-model-training-and-serving).

The [Vega/semantic image](model-container-guide.md) has its own CUDA dependencies and GPU checks. Native Laya and Unsloth need separately validated Linux training images.

## Start your own workspace

Install Docker Engine/Desktop with Linux containers and Docker Compose supporting `--wait`. Start the engine. Run the following from the repository root in a Python environment installed according to [local setup](setup.md):

```bash
domain-platform provision-key --file data/config/operator-keys.json --tenant my-enterprise --actor owner --role admin
docker compose up --build --detach --wait
```

Provisioning creates the configuration directory and prints a bearer credential once; retain it privately. The credential file is mounted read-only into the services. Open the browser at `http://127.0.0.1:8501`, connect to the displayed workspace API and enter the credential. The web service calls the API using the internal `http://api:8080` address. Host clients use `http://127.0.0.1:8080`.

The three services share the named `workspace` volume. API and worker use `/app/data/workspace`; configured credentials and provider profiles remain separate operator configuration. Compose reads only the environment variables explicitly mapped in [compose.yaml](../compose.yaml). Configure optional providers according to their guides; a hosted call or cloud training job has its own data/compute approval workflow.

Inspect services and logs with:

```bash
docker compose ps
docker compose logs --tail 100 api worker web
```

The API checks storage/authentication/collector readiness, and the worker records its heartbeat independently. A running container or web health response does not establish that a trained model or GPU is ready. Treat startup, authentication, jobs, releases and inference as separate checks.

Stop without deleting the named workspace volume:

```bash
docker compose down
```

Use the same Compose project identity when reopening the workspace. Named volumes survive ordinary container replacement and `down`; adding `--volumes` deletes them. Back up before deliberate data removal or migration. [Compose lifecycle](https://docs.docker.com/reference/cli/docker/compose/up/), [down/volume semantics](https://docs.docker.com/reference/cli/docker/compose/down/), [operator recovery](recovery-guide.md)

## Verify a build without touching your workspace

Build the local image, then run the maintained acceptance command:

```bash
docker build --platform linux/amd64 --tag domain-decision-platform:local .
python scripts/verify_containers.py --image domain-decision-platform:local
```

[verify_containers.py](../scripts/verify_containers.py) creates a uniquely named Compose project, a fresh volume, temporary hashed credentials and dedicated networking with available loopback ports. The worker joins an internal network; API/browser also join an entrypoint bridge. It uses no configured teacher, hosted decision provider or cloud profile. Its handwritten examples carry explicit fixture markers; simulated operational-origin flags exercise the reservation machinery, not real enterprise quality. Select an explicit context with `--docker-context` when multiple Docker engines are installed. [Docker Compose networks](https://docs.docker.com/reference/compose-file/networks/).

The check performs these actual operations:

1. Start API/worker/web, inspect readiness and web health, confirm non-root execution and match package/browser source to the worktree.
2. Upload 180 fictional positive records and approve them through the reviewer credential; prepare four roles in the independent container worker.
3. Upload 40 fictional applicability negatives, reject forged imported approval metadata, review them and prepare the paired eight-file archive with verified hashes. Check that unauthorized tenants and data roles are denied access.
4. Train the CPU specialist, activate it through the owner credential and call all three decision types through the inference credential.
5. Replace all three containers while retaining their named volume. Compare released predictions and the applicability archive manifest before and after replacement.
6. Create a consistent backup, restore it into a new workspace and compare cold restored predictions.
7. Check each container's unique Compose project label before removing only the check's containers, networks and volume. The application image is retained.

Each run retains its receipt, generated configuration and private Docker log under a new directory in `data/container-checks`. `--output` changes that parent; existing runs are not overwritten. A failed operation or failed cleanup produces a nonzero exit. If cleanup fails, inspect the recorded project and resources before removing anything. The check does not delete your normal application workspace.

The same maintained command has an explicit Vega route using a retained model image, approved offline cache and selected local GPU; follow the [model image lifecycle check](model-container-guide.md#verify-authenticated-container-lifecycle). The default CPU route remains separate.

The CI workflow includes a Docker build and the maintained acceptance command. Confirm the GitHub Actions result for your revision; local execution does not establish a hosted CI pass.

## Run the full Linux regression

After building the image, run this from the repository root in a POSIX shell. It mounts the fictional tests and required public contract files; it uses the application's installed source and examples from the image. Test services communicate over container-local loopback, with no external network or host ports:

```bash
docker run --rm --network none --read-only \
  --tmpfs /tmp:rw,nosuid,size=4g --memory 2g --cpus 2 --pids-limit 256 \
  --cap-drop ALL --security-opt no-new-privileges \
  --env PYTHONDONTWRITEBYTECODE=1 \
  --mount "type=bind,source=$(pwd)/tests,target=/app/tests,readonly" \
  --entrypoint python domain-decision-platform:local \
  -m unittest discover -s tests -v
```

The temporary filesystem's size allows the existing one-GiB-plus-artifact storage preflight to run; the memory limit remains separate. This is CPU/fixture regression acceptance, including mocked provider transports and declared-vector semantic tests. Actual cloud allocation, real GPU/encoder execution and representative domain gold require their own qualifications.
