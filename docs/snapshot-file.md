# Snapshot File

After every refresh, the app writes the usage state of each account to a JSON file. Another program on the same computer can read these files. That program needs no OAuth token and makes no call to the usage endpoint. Each caller of the endpoint uses the same rate limit of the account. A program that reads the file adds no load to that limit.

## Location

| Platform | Folder |
|----------|--------|
| Windows | `%LOCALAPPDATA%\usage-monitor-for-claude\snapshots\` |
| Linux | `~/.cache/usage-monitor-for-claude/snapshots/` |

The folder holds one file per account. The file name is the account label with `.json` added. Each character outside `A-Z`, `a-z`, `0-9`, `-`, `_` and `.` becomes `_`.

The label of the default Claude configuration directory, `~/.claude`, is `default`. Every other directory uses its folder name, so `C:\Users\you\.claude-2` gives `.claude-2.json`. If two folders have the same name, the second label gets the suffix ` (2)`.

## Format

```json
{
  "version": 2,
  "written_at": "2026-09-25T08:14:03.512345+00:00",
  "account": {
    "label": "default",
    "email": "you@example.com",
    "plan": "Claude Team"
  },
  "error": null,
  "data": {
    "five_hour": { "utilization": 12.0, "resets_at": "2026-09-25T10:20:00+00:00" },
    "seven_day": { "utilization": 29.0, "resets_at": "2026-09-29T14:00:00+00:00" }
  }
}
```

| Field | Content |
|-------|---------|
| `version` | The schema version of the file. The current version is `2` |
| `written_at` | The UTC time of the write, in ISO 8601 |
| `account.label` | The account label that the popup shows |
| `account.email` | The email address of the account, or `null` until the account profile loads |
| `account.plan` | The plan name as the popup shows it, for example `Claude Max`, or `null` until the account profile loads |
| `error` | The error text of the last refresh, or `null` after a good refresh |
| `data` | The usage response of the API, unchanged |

After an error, `data` holds `error`. After HTTP 429, `data` also holds `rate_limited: true` and `retry_after` in seconds. After a good refresh, each quota is an object with `utilization` in percent and `resets_at`. A quota that the account does not have is `null`.

For the Codex account, `data` holds the Codex limits in the same shape. The app names each field after the length of its window, for example `five_hour` or `seven_day`. A limit with another name adds that name, for example `seven_day_gpt_5_codex`. The file layout and the schema version stay the same.

## Rules for a reader

- Read the complete file, then parse it. The app writes a temporary file in the same folder and then replaces the old file in one step. A reader therefore never gets a half-written file.
- Skip a file with a `version` that your program does not know. A change to the fields outside `data` increases the version.
- Treat `data` as the API response. Anthropic can add, remove or rename a quota field at any time, and the version stays the same.
- Use `written_at` to find old data. The app writes the file after each fetch of the usage data. An age much larger than the [poll interval](configuration.md#polling-intervals) means that the app does not run or cannot poll.
- Do not write to the folder. The app reads its own file at startup and uses it to delay the first poll.

The app never deletes a snapshot file. If you remove an account from `--config-dir`, its file stays until you delete it.

## What the file holds

The file holds the account email address, the plan name and the usage numbers. It holds no token and no credential.
