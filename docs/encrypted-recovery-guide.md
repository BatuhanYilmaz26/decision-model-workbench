# Encrypt and recover a local workspace

Capture a consistent workspace and its registered model exports into an encrypted bundle, verify it using a separately retained receipt, and recover into a new directory. These are filesystem-level operator commands: the tenant API does not expose all-tenant backup downloads.

The encrypted workflow preserves the existing [backup scope, validation and interrupted-job rules](recovery-guide.md). It includes private domain records from every tenant in the workspace. Registered model data are checked against the captured registry; credentials, caches and incomplete training checkpoints remain outside recovery.

## Prepare private storage and a recovery key

Install the CPU [platform dependency profile](../requirements/README.md). It includes Cryptography 50.0.2. Choose approved local storage with sufficient free space and access restricted to the backup operator. Temporary plaintext is required: use an encrypted volume when that is required by your data policy.

Generate a new key file:

```bash
domain-platform create-backup-key --output .internal/backup-key.dmtkey
```

The command creates a versioned binary file containing a randomly generated 256-bit secret and prints only its path and key identifier. It refuses to overwrite an existing file. POSIX creation mode is `0600`; Windows operators must configure appropriate NTFS permissions because POSIX mode does not establish Windows ACLs. [Windows access-control management](https://learn.microsoft.com/en-us/windows-server/administration/windows-commands/icacls)

Keep the key in approved secret storage, with a separate recoverable copy. Do not store it alongside a distributed backup or substitute a password/text file. Losing the key makes its backups unrecoverable. Create a new key file for future captures when rotating; retain each older key for the backups that still require it. Enterprise KMS integration and automatic key rotation are separate deployment work.

## Capture an encrypted backup

Choose a destination that does not exist and is outside the source workspace:

```bash
domain-platform backup-encrypted --home data/workspace --key-file .internal/backup-key.dmtkey --output backups/domain-20261014 --timeout 900
```

Use a new destination for every capture. The source API may stay running: SQLite supplies a consistent captured database, and only model exports referenced by that snapshot are copied. Avoid external modifications or deletion of registered exports during capture.

The completed directory contains:

| File | Purpose |
|---|---|
| `payload.dmtenc` | Encrypted captured manifest and file contents |
| `encrypted-backup.json` | Format, key identifier, ciphertext size/checksum, captured-manifest checksum and capture time |

The command returns `encrypted_manifest_sha256`, `key_id`, `payload_bytes` and the destination. Retain that returned checksum and key identifier separately from the bundle, under approved access controls. A checksum copied from the bundle itself does not establish trusted provenance. The metadata exposes size, capture time and hashes; filenames and domain contents are encrypted.

Copy both files when moving a bundle. Missing/extra entries or altered ciphertext are refused. Completion metadata is published last. A caught publication failure cleans the new destination; existing directories are never overwritten.

## Verify before recovery

Create a private scratch-parent directory on approved storage outside the backup. Then supply the checksum retained at capture:

```bash
domain-platform verify-encrypted-backup --backup backups/domain-20261014 --key-file .internal/backup-key.dmtkey --encrypted-manifest-sha256 YOUR_RETAINED_SHA256 --scratch-parent .internal/recovery-scratch --timeout 900
```

Verification checks the retained metadata identity, key identity, complete authenticated stream and exact ciphertext checksum. Only then does it interpret plaintext file records. It refuses unsafe paths, excessive sizes, missing/trailing contents or file-hash mismatches and performs the existing read-only database/registry/model validation. A successful result contains `verified: true`, the key identifier and captured summary. It does not load a GPU or establish prediction quality.

Temporary payload/file copies are removed on ordinary success or caught failure. A killed process or power loss can leave scratch directories; inspect and remove abandoned `.dmt-*` directories after confirming no recovery process still uses them. Directory removal does not guarantee secure erasure or remove filesystem snapshots.

## Restore into a fresh workspace

Use the corresponding key and separately retained checksum:

```bash
domain-platform restore-encrypted --backup backups/domain-20261014 --key-file .internal/backup-key.dmtkey --encrypted-manifest-sha256 YOUR_RETAINED_SHA256 --home data/recovered-workspace --timeout 900
```

Authentication and workspace validation must succeed before the destination is published. The command preserves active release identity, data/review history and frozen gates; copied pending/running jobs become interrupted and cannot silently replay teacher or training calls. Supported schema-3/4/5/6/7 backups use the current recovery path. The result includes the ordinary restore receipt plus the encrypted manifest checksum and key identifier.

Reconfigure trusted credentials/provider secrets, start the API and worker against the recovered home, check `/ready`, and compare known authenticated decisions with your pre-backup probe. Follow the [recovery startup checklist](recovery-guide.md#restore-and-bring-services-back). Keep the original workspace until you have accepted the recovered service.

## Capacity and encryption format

The format retains the existing limits of 50,000 captured files, 500 GiB of file contents and a 16 MiB captured manifest. These are refusal bounds, not scale or recovery-time guarantees. Allow approximately twice the captured payload size in free space for capture/verification staging and three times for restore staging and the recovered copy. The source and completed backup consume additional space. Uncompressed model exports can dominate these requirements.

The deadline is checked between bounded encryption/decryption/copy stages and before publication; an operating-system file copy or existing workspace-validation call is not forcibly interrupted. Measure capture and recovery time on your actual storage and model exports.

The project-specific v1 envelope uses HKDF-SHA256 to derive a fresh AES-256 key from the private root key and a random per-backup salt. One-MiB records use unique 96-bit counter nonces and full 128-bit authentication tags; associated data binds the header, position and record framing. A required authenticated footer binds count, total length and payload hash. Reordering, truncation and trailing records are refused. Keep a compatible application version to read this format. [Cryptography AES-GCM documentation](https://cryptography.io/en/stable/hazmat/primitives/aead/), [HKDF documentation](https://cryptography.io/en/stable/hazmat/primitives/key-derivation-functions/)

For interval captures and verified retention, use the [scheduled backup service](scheduled-backups-guide.md). Offsite replication, external alerts, enterprise key custody and power-loss/large-scale acceptance must be configured and qualified for your deployment. The [source metadata](../research/provenance/encrypted-recovery-sources-2026-10-14.json) records the primary interfaces used here.
