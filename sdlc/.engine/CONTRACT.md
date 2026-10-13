# opieworks-sdlc engine contract

This file is the interface for the engine modules. Each module author follows it. Do not change a signature here without a change to this file.

## 1. Rules for all modules

- Use the Python standard library only. The minimum is Python 3.11. Test on 3.11, 3.12, and 3.14.
- Import shared helpers from `sdlc_lib.common`. Do not copy them.
- Raise `SdlcError(message, code=1)` for every expected failure. The message is one STE sentence or two. The CLI prints `error: <message>` and exits with `code`.
- Do not print from library functions. Return data. `sdlc.py` prints.
- Never print or write a token value. Print key names only.
- Paths in return values are `pathlib.Path`, except where this file says "repo-relative POSIX string".
- Build every path from a user value or a stage file with `common.safe_join`.
- Build every path that the engine writes in a target repo with `common.safe_write_path`. Never write or delete through a symlink in the repo.
- Write files with `common.write_if_changed` so that a second run gives no diff (NFR-3).
- Tests go in `tests/test_<module>.py`. Use `unittest` and temp folders. Use `tests/fixtures/stage-demo/` as the stage. Use fake tokens such as `fake-bot-token-123`.
- The engine files ship in every bundle. They must pass `leak_scan` and `lint_portable`. Do not write a home path, an email, a token shape, or a harness-specific term as a literal in engine source. Build such pattern strings from parts.
- Write prose with no em dashes, no semicolons, and no contractions.

## 2. Fixed names

| Name | Value |
|---|---|
| Overlay folder | `sdlc/` (`common.OVERLAY_DIR`) |
| Overlay folders | `sdlc/templates/`, `sdlc/agents/` (`common.OVERLAY_PREFIXES`) |
| Repo config | `sdlc/config.toml` (`common.CONFIG_REL`) |
| Engine copy | `sdlc/.engine/` (`common.ENGINE_REL`) |
| Installed stage defaults | `sdlc/.engine/defaults.toml` (`common.DEFAULTS_REL`) |
| Work items | `sdlc/work/<id>/` (`common.WORK_REL`) |
| Bot token file default | `~/.config/opieworks-sdlc/bot.env` (`common.DEFAULT_BOT_TOKEN_FILE`) |
| Leak denylist default | `~/.config/opieworks-sdlc/leak-denylist.txt` (`common.DEFAULT_LEAK_DENYLIST`) |
| Leak denylist env override | `OPIEWORKS_SDLC_LEAK_DENYLIST` (`common.LEAK_DENYLIST_ENV`) |
| Folders that export and engine copy skip | `tests`, `__pycache__`, `.git` (`common.EXCLUDED_DIRS`) |
| Files that export and engine copy skip | `.DS_Store`, `Thumbs.db` (`common.JUNK_FILES`), and `*.pyc` |

## 3. stage.toml format

```toml
[stage]
id = "demo"              # lowercase letters, digits, hyphens
version = "0.1.0"        # string

[agents_md]
text = """
Section body for AGENTS.md.
"""

[codeowners]
paths = [".github/", "sdlc/.engine/", "sdlc/agents/", "sdlc/config.toml"]

[[install]]
src = "github/workflows/sdlc-demo.yml"   # relative to the stage folder
dest = ".github/workflows/sdlc-demo.yml" # relative to the repo
kind = "managed"                         # "managed" or "user"

[config_defaults.demo]
verify = "make test"
```

`[agents_md]`, `[codeowners]`, `[[install]]`, and `[config_defaults]` are optional. `common.load_stage(stage_dir)` reads and checks the file. It returns:

```python
{"id": str, "version": str, "agents_md": str | None, "codeowners": list[str],
 "install": [{"src": str, "dest": str, "kind": "managed" | "user"}],
 "config_defaults": dict}
```

## 4. Marker format

| File | Begin line | End line | `style` |
|---|---|---|---|
| `AGENTS.md` | `<!-- opieworks-sdlc:<stage>:begin -->` | `<!-- opieworks-sdlc:<stage>:end -->` | `"md"` |
| `.github/CODEOWNERS` | `# opieworks-sdlc:<stage>:begin` | `# opieworks-sdlc:<stage>:end` | `"hash"` |

Each marker is a full line that starts at column 0. Trailing spaces are allowed. In a markdown file, a marker line inside a fenced code block (``` or ~~~) is an example, not a marker. A file has zero or one pair per stage. Text outside the markers belongs to the user. Only `common.set_section` changes a section.

## 5. common (complete)

| Signature | Returns | Raises |
|---|---|---|
| `SdlcError(message: str, code: int = 1)` | exception with `.message` and `.code` | |
| `repo_root(explicit=None, start=None) -> Path` | the explicit folder, else the first parent of `start` (default: cwd) with a `.git` entry | `SdlcError` when the folder is missing or no repo is found |
| `safe_join(base, relpath: str) -> Path` | `base / relpath` | `SdlcError` on an empty, absolute, drive, backslash, NUL, or `..` path. The check is lexical |
| `safe_write_path(base, relpath: str, allow_leaf_link=False) -> Path` | `safe_join(base, relpath)` | `SdlcError` as `safe_join`, and when a part of `relpath` that exists below `base` is a symlink. With `allow_leaf_link=True`, the last part may be a symlink |
| `iter_files(root, exclude_dirs=EXCLUDED_DIRS, followlinks=True) -> list[Path]` | sorted relative file paths. Follows symlinks unless `followlinks` is False. Skips `.pyc` and `JUNK_FILES` | |
| `write_if_changed(path, data: str \| bytes) -> bool` | True when it wrote. Makes parent folders. No newline translation | `OSError` |
| `load_toml(path) -> dict` | parsed TOML | `SdlcError` when missing or not valid |
| `deep_merge(base: dict, over: dict) -> dict` | a new dict. Tables merge. Other values replace | |
| `installed_defaults(repo) -> dict` | the tables of `sdlc/.engine/defaults.toml` as `{stage id: config_defaults}`. A missing file gives `{}` | `SdlcError` on bad TOML or a stage value that is not a table |
| `load_config(repo, defaults: dict \| None = None) -> dict` | `CORE_DEFAULTS`, then each table of `installed_defaults(repo)` in stage id order, then `defaults`, then `sdlc/config.toml`. See section 7a | `SdlcError` on bad TOML |
| `flatten_keys(data: dict, prefix="") -> list[str]` | sorted dotted names of all leaf values. An empty table is a leaf | |
| `dump_toml(data: dict) -> str` | TOML text. `tomllib.loads(dump_toml(d)) == d` | `SdlcError` on unsupported values (None, arrays of tables) |
| `load_stage(stage_dir) -> dict` | see section 3 | `SdlcError` on any bad field, a section of the wrong type, or an unsafe path |
| `markers(stage: str, style: str) -> tuple[str, str]` | the begin and end lines | `SdlcError` on an unknown style or bad stage id |
| `get_section(text, stage, style) -> str \| None` | the text between the markers, or None | `SdlcError` on broken markers |
| `set_section(text, stage, body, style) -> str` | new text. Replaces the section or appends it after one blank line. New lines use the newline style of the begin marker, else of the first line (CRLF or LF). A UTF-8 BOM before a marker on the first line is kept. Idempotent | `SdlcError` on broken markers |
| `parse_frontmatter(text) -> tuple[dict, str]` | (frontmatter, body). `({}, text)` with no frontmatter | `SdlcError` on bad frontmatter |

Frontmatter grammar: a `---` line, then lines of `key: value`, `key: [a, b]`, `key: true` or `false`, a quoted string, or `key:` followed by lines of `  - item`, then a `---` line. Values are strings, booleans, or lists of these.

## 6. overlay

| Signature | Behavior |
|---|---|
| `resolve(relpath: str, stage_dir: Path, repo: Path) -> Path` | Return `<repo>/sdlc/<relpath>` when it is a file. Else return `<stage_dir>/<relpath>` when it is a file. Else raise `SdlcError` |
| `eject(relpath: str, stage_dir: Path, repo: Path) -> Path` | Copy the bundle default to `<repo>/sdlc/<relpath>` byte for byte. Return the new path. Raise `SdlcError` when the default is missing or the overlay file exists |

Both reject an unsafe `relpath` through `safe_join`. Both raise `SdlcError` when the first folder of `relpath` is not `templates` or `agents` (the folders of `OVERLAY_PREFIXES`), because setup owns the other paths under `sdlc/`.

## 7. install

```python
@dataclass
class SetupReport:
    written: list[str]          # repo-relative POSIX paths that changed
    removed: list[str]          # stale engine copy files that setup deleted
    skipped: list[str]          # user-owned files that exist and stay as they are
    new_config_keys: list[str]  # dotted keys in config_defaults that config.toml lacks
    config_created: bool
    pr_url: str | None = None   # the setup PR, when open_pr=True

def setup(stage_dir: Path, repo: Path, open_pr: bool = False) -> SetupReport
```

Steps, in this order:

1. If `open_pr` is True, return `setup_pr.open_setup_pr(stage_dir, repo)` (section 13).
2. `stage = common.load_stage(stage_dir)`.
3. For each `[[install]]` entry: `managed` means write every time with `write_if_changed`. `user` means write only when `dest` is absent, else add it to `skipped`.
4. Never write under `sdlc/templates/` or `sdlc/agents/` (the overlay).
5. `AGENTS.md`: `set_section(text, stage.id, stage.agents_md, "md")` when `agents_md` is set. Make the file when absent. When `AGENTS.md` is a symlink, for example to `CLAUDE.md`, write the link target. The target must be in the repo, outside `.git`, and not a reserved path. Else raise `SdlcError`. The symlink stays as it is.
6. `.github/CODEOWNERS`: `set_section` with style `"hash"`. The body is one line per path: `<path> @<owner>`. The owner comes from config key `codeowners.owner`. When the key is absent, write the path lines with the placeholder owner `@OWNER` and add `codeowners.owner` to `new_config_keys`. When the repo already uses a root `CODEOWNERS` or `docs/CODEOWNERS`, the section goes in that file, and the body gets one more line that gives that file to the owner, for example `/CODEOWNERS @<owner>`.
7. Engine copy: make `sdlc/.engine/` match the core folder (the folder that holds `sdlc.py`) exactly, with `iter_files` and `EXCLUDED_DIRS`. Delete files in `sdlc/.engine/` that the core does not have and list them in `removed`. The one exception is `defaults.toml`. The sync keeps it. The names compare by exact case. The delete pass runs before the copy pass, so a case-insensitive disk cannot lose a file that only changed case. `VERSION` is part of the copy.
8. `sdlc/config.toml`: when absent, write `dump_toml(stage.config_defaults)` and set `config_created`. When present, never change it. Report the keys of `flatten_keys(config_defaults)` that `flatten_keys(existing)` lacks.
8a. `sdlc/.engine/defaults.toml` (managed): set the table `[<stage id>]` to `stage.config_defaults`. Keep the tables of the other stages. Write a fixed header comment, then the tables sorted by stage id, with `write_if_changed`. Add the path to `written` when it changes. Section 7a tells why.
9. A second call with no changes writes nothing and returns an empty `written` and `removed`.
10. All checks run before the first write. Every destination goes through `safe_write_path`. Setup refuses `sdlc/.engine/` when it, a parent, or anything inside it is a symlink. The `config.toml` text is made before the first write.

## 7a. Stage defaults at run time

A stage command reads a config key from `sdlc/config.toml` first. When the key is absent there, it uses the `config_defaults` of the installed stages. The repo value always wins. `common.load_config` is the one helper for this. Every command reads its config through it.

Setup never edits an existing `sdlc/config.toml`. So a stage that a repo installs after the file exists gets no keys in it. Thus setup records the defaults of each stage in `sdlc/.engine/defaults.toml`, and `load_config` reads them from there.

Why a file in the repo, and not the stage folder:

- The engine copy in `sdlc/.engine/` has no stage folder. The CI jobs (`checks`, `detect`, `deploy build-docs`) run from it. A file in the repo works for the engine copy and for a skill command in the same way. So local runs and CI runs read the same values.
- The file holds only the stages that the repo installed. The stage folders next to a skill can hold stages that the repo did not install.
- `sdlc/.engine/` is already managed and owned by the gate owner in CODEOWNERS. So the file needs no new CODEOWNERS path.

The file is managed, not user-owned. Do not edit it. Setup rewrites the table of its stage every time. Put a team value in `sdlc/config.toml`. When two stages give the same key, the stage with the later id wins. The stages now give the same `agent_runner` tables.

A repo that ran setup before engine 0.5.1 has no `defaults.toml`. Run setup again for each installed stage. Until then, a missing key gives the same error as before, for example `The config has no deploy.nit_cap.`.

## 8. workitem

```python
LANES = ("fast", "full")

@dataclass
class Artifact:
    name: str               # file name, for example "intent.md"
    work_item: str | None   # value of the work_item frontmatter key
    upstream: list[str]     # upstream links. Empty means "none"

def slugify(title: str) -> str
def new_item(title: str, lane: str, repo: Path, today: datetime.date | None = None) -> str
def show_item(item_id: str, repo: Path) -> list[Artifact]
```

- ID format: `YYYYMMDD-<slug>`. `today` defaults to `datetime.date.today()`.
- `slugify`: lowercase ASCII letters and digits, runs of other characters become one hyphen, no hyphen at either end, at most 48 characters. An empty slug raises `SdlcError`.
- `new_item` makes `sdlc/work/<id>/item.toml` with `id`, `title`, `lane`, and `created` (ISO date). It raises `SdlcError` on an unknown lane or when the folder exists.
- Artifact frontmatter: every artifact (`*.md` in the folder) starts with frontmatter that has `work_item: <id>` and `upstream:`. The `upstream` value is a list of relative links, or the string `none, used <fallback>` (FR-3). `show_item` maps that string to an empty list.
- `show_item` raises `SdlcError` when the folder does not exist.

## 9. role

```python
CAPABILITIES = ("read", "run", "edit", "github-bot")
HUMAN_TOKEN_VARS = ("GH_TOKEN", "GITHUB_TOKEN", "GH_ENTERPRISE_TOKEN", "GITHUB_ENTERPRISE_TOKEN")
ASKPASS_VARS = ("GIT_ASKPASS", "SSH_ASKPASS", "SSH_ASKPASS_REQUIRE")
ASKPASS_PREFIX = "VSCODE_GIT_"
ISOLATION_ENV = {"GH_PROMPT_DISABLED": "1", "GIT_TERMINAL_PROMPT": "0", "SSH_ASKPASS_REQUIRE": "never"}
GIT_ISOLATION_CONFIG = (("credential.helper", ""), ("core.askPass", ""))
BASE_ENV_ALLOWLIST = ("PATH", "HOME", "USER", "LOGNAME", "SHELL", "TMPDIR", "TMP", "TEMP",
                      "LANG", "TERM", "COLORTERM", "TZ", "NO_COLOR", "GIT_CONFIG_COUNT")
BASE_ENV_PREFIXES = ("LC_", "GIT_CONFIG_KEY_", "GIT_CONFIG_VALUE_")
DRY_RUN_TOKEN = "dry-run"

@dataclass
class RoleSpec:
    name: str
    stage: str
    capabilities: list[str]
    fresh_context: bool
    inputs: list[str]
    outputs: list[str]
    stop_when: list[str]
    body: str
    path: Path | None

def parse_role_spec(path: Path) -> RoleSpec
def read_bot_token(path: str | Path) -> str
def build_invocation(spec, harness: str, config: dict, inputs=(), base_env=None, dry_run=False, task=None) -> tuple[list[str], dict[str, str]]
def allowed_env_names(harness: str, config: dict) -> set[str]
def execute(argv: list[str], env: dict[str, str], cwd: Path) -> int
def cleanup(env: dict[str, str]) -> None
```

- `parse_role_spec` uses `common.parse_frontmatter`. All seven keys are required. `capabilities` is a non-empty list and a subset of `CAPABILITIES`. An unknown capability raises `SdlcError` that names it. `fresh_context` is a boolean.
- `read_bot_token` expands `~`. It reads the line `GH_TOKEN=<value>`. It skips blank lines and `#` lines. It raises `SdlcError` when the file or the line is missing. The message never holds the value.
- `build_invocation`:
  1. Read the table `config["agent_runner"][harness]`. Raise `SdlcError` when it is absent.
  2. `argv = command + flags + [prompt]`. `flags` is the map of the strongest of `edit`, `run`, `read` in the spec. A missing map for a needed capability raises `SdlcError`.
  3. `prompt` is the spec body, then (when `task` is set) a line `Task:`, the task text, and a blank line, then a line `Inputs:`, then one line per input path.
  4. `env` is a filtered copy of `base_env` (default: `os.environ`) plus `ISOLATION_ENV`. The copy keeps only allowlisted names (D22). `allowed_env_names` gives the allowlist: `BASE_ENV_ALLOWLIST`, the `env` list of `[agent_runner.<harness>]`, and the `env` list of `[roles]` in `sdlc/config.toml`. A name that starts with a part in `BASE_ENV_PREFIXES` also passes. Each `env` list is optional. A value that is not a list of strings, or a `[roles]` value that is not a table, raises `SdlcError`. An allowlisted name that the parent does not have is not added. Then the copy drops the names in `HUMAN_TOKEN_VARS` and `ASKPASS_VARS`, every name that starts with `ASKPASS_PREFIX`, every name with a `GH` or `GITHUB` word and a `TOKEN` or `PAT` word, and every value with the shape of a GitHub token (the `token:github` leak patterns). This GitHub credential check runs also for a name that the config allows. Then the entries of `GIT_ISOLATION_CONFIG` go at index n and up, where n is the `GIT_CONFIG_COUNT` of the copy (0 when it is absent or not a number that is 0 or more). `GIT_CONFIG_COUNT` becomes n plus 2. The entries below n stay.
  5. Only when `github-bot` is in the capabilities: set `env["GH_TOKEN"]` to `read_bot_token(config["bot"]["token_file"])`. With `dry_run=True`, do not read the file. Set `env["GH_TOKEN"]` to `DRY_RUN_TOKEN`.
  6. Set `env["GH_CONFIG_DIR"]` to a new empty temp folder, so `gh` cannot find the stored human login. The caller removes it with `cleanup(env)`.
- `execute` runs `subprocess.run(argv, env=env, cwd=cwd)` and returns the exit code. A missing command raises `SdlcError`.
- No-edit check (D20). `execute_checked(spec, argv, env, cwd) -> int` runs `execute`. When `edit` is not in the capabilities (`needs_no_edit_check`), it takes `snapshot(cwd)` before and after the role: the HEAD commit, and for each path in `git status --porcelain=v1 -z --untracked-files=all` its status and a SHA-256 of its content. So a further edit to a file that was already dirty is found. `changed_paths(before, after)` lists every path whose status or content changed, and HEAD when it moved, except paths under `sdlc/work/`. When the list is not empty, it raises `NoEditViolation` (an `SdlcError`) with the exit code `NO_EDIT_EXIT` (3). The message names the role, says "Review or discard these changes. The engine did not undo them.", and lists one path per line. The check detects only. It never reverts. It runs for every harness and needs no harness flag. Files that `.gitignore` ignores are out of scope. A role with `edit` is not checked. A dry run does not run the role and does not check.

## 10. scan

```python
@dataclass(frozen=True)
class Hit:
    path: str     # POSIX path relative to the scanned folder
    line: int     # 1-based
    pattern: str  # pattern name, for example "home-path" or "token:github"
    def format(self) -> str  # "path:line:pattern"

def leak_scan(root: Path, allowlist: list[str] | None = None, denylist: list[str] | None = None) -> list[Hit]
def lint_portable(root: Path) -> list[Hit]
```

- Both walk `root` with `iter_files` (follows symlinks, skips `tests`, `__pycache__`, and `.git`). `leak_scan` scans two variants of every file: the normal decode (UTF-16 or UTF-32 when the file starts with a BOM, else UTF-8), and the bytes of the whole file without NUL bytes, decoded as Latin-1. It reports a file that starts with the magic bytes of a compressed or archive format (gzip, zip, bzip2, xz, 7z, zstd, git pack), or a file that is all zlib data (for example a git loose object), as line 0 with the pattern `archive`, because the scan cannot see inside it. For a PDF file, it also inflates each zlib `stream` and scans the text at the line where the stream starts. For `lint_portable`, a file with a NUL byte in the first 8 KiB decodes as UTF-16 or UTF-32 when it starts with a BOM. Else it is binary and `lint_portable` skips it. Results sort by path, then line, then pattern.
- `leak_scan` also checks each relative file path and reports a hit there as line 0. It checks each line and its URL-decoded form.
- `denylist_path() -> str` returns the env value, else the default path.
- `leak_scan` built-in patterns: absolute home paths (Unix user home roots, `/var/home`, the macOS forms under `/System/Volumes/Data/` and `/Volumes/<disk>/`, the Windows users folder, and its WSL form under `/mnt/<drive>/`), email addresses, and token shapes (GitHub classic, OAuth, and fine-grained tokens, Anthropic and generic secret-key prefixes, Slack tokens, AWS access key IDs, PEM private key headers).
- `allowlist` default: the lines of `leak-allowlist.txt` next to `sdlc.py`. An allowlist entry stops a hit only when the matched text lies fully inside a case-insensitive occurrence of the entry on the same line. So with the denylist word `Jo` and the allowlist entry `JoTool`, the line `JoTool and JOTOOL_HOME` gives no hit, but `Jo Smith` gives a hit.
- `denylist` default: the lines of the file in env `OPIEWORKS_SDLC_LEAK_DENYLIST`, else `~/.config/opieworks-sdlc/leak-denylist.txt`. A missing file means an empty list. Each entry is a case-insensitive match. A letter must not come before it, and a lowercase letter must not come after it. So digits, `_` and camelCase do not hide an entry. The words of a multi-word entry match with no separator, or with any run of spaces, `_`, `.` or `-` between them. The pattern name is `denylist`, never the word itself.
- `lint_portable` uses the portability denylist from the build spec (NFR-1). It does not report a line inside a markdown section titled "Harness notes (optional)". That section ends at the next heading of the same or a higher level. It does not report a line inside a TOML table whose header path has an `agent_runner` segment, for example `[agent_runner.x]` or `[config_defaults.agent_runner.x]`.

## 11. export

```python
def export(skill_dir: Path, out: Path, require_denylist: bool = True) -> Path
```

1. Raise `SdlcError` when `out` exists. With `require_denylist=True`, raise `SdlcError` when the leak denylist is missing or empty.
2. Stage the copy in a temp folder next to `out`. Copy `skill_dir` with `iter_files`, so the `core` symlink becomes real files and `tests`, `__pycache__`, and `.git` drop out.
3. Write `PORTING.md` at the bundle root from `core/templates/PORTING.md` with `render_porting(template, skill_dir)`. Template lines between `{{if <kind>}}` and `{{end}}` stay only when the kind holds. The kinds are `hub` (no `stage.toml`), `stage` (a `stage.toml`), `scaffold` (the `SKILL.md` description starts with `SCAFFOLD ONLY`), and `built` (a stage that is not a scaffold). Blocks can nest. An unknown kind or an unbalanced block raises `SdlcError`.
4. Run `leak_scan` and `lint_portable` on the staged copy.
5. On any hit: delete the staged copy and raise `SdlcError` with the first hit as `path:line:pattern` and the hit count. Nothing stays at `out`.
6. Else give the staged folder the normal folder mode (0777 without the umask bits), rename it to `out` and return `out`.

## 12. CLI

| Command | Calls |
|---|---|
| `setup <stage-dir> [--repo PATH] [--open-pr]` | `install.setup` |
| `resolve <relpath> [--stage-dir] [--repo]` | `overlay.resolve` |
| `eject <relpath> [--stage-dir] [--repo]` | `overlay.eject` |
| `work new <title> --lane fast\|full [--repo]` | `workitem.new_item` |
| `work show <id> [--repo]` | `workitem.show_item` |
| `role <name-or-path> --harness <h> [--stage-dir] [--input FILE ...] [--task TEXT] [--repo] [--dry-run]` | `overlay.resolve("agents/<name>.md")` for a name, `role.parse_role_spec`, `common.load_config`, `role.build_invocation`, `role.execute_checked`, `role.cleanup`. Exit 3 on a no-edit violation. A value with a `/` or a `.md` suffix is a path |
| `leak-scan <dir>` | `scan.leak_scan`. Exit 1 on any hit. A notice on stderr when the denylist is missing or empty |
| `lint-portable <dir>` | `scan.lint_portable`. Exit 1 on any hit |
| `export <skill-dir> --out <dir> [--no-denylist]` | `export.export` |
| `push [--branch B] [--repo]` | `github.push` |
| `pr --title T [--body B \| --body-file F] [--head] [--base] [--label L ...] [--repo]` | `github.open_pr`. Prints the URL |
| `gates apply [--repo]` | `gates.apply` |
| `check-bot [--repo]` | `gates.check_bot`. Prints one line per rule. Exit 1 on any FAIL |
| `checks [--base SHA --head SHA] [--repo]` | The `sdlc-checks` CI job (section 15). Runs the test-lock seam (S3 fills it) and `evals.eval_gate`. With `--base` and `--head`, it reads a local repo through `evals.GitSource`. Else it reads the pull request in `GITHUB_EVENT_PATH` through `evals.ApiSource`. With neither, it prints a notice and exits 0. Exit 1 on any FAIL |
| `verify <id> [--upstream LINK ...] [--fallback TEXT] [--max-runs N] [--stage-dir] [--repo]` | `verify.settings`, `verify.run_verify`, `verify.write_evidence`. Exit 1 when the verdict is FAIL |
| `propose-verify [--repo]` | `verify.propose`. Prints one proposal per line |
| `run-evals --harness <h> [--task NAME ...] [--stage-dir] [--repo]` | `evals.run_evals` |
| `version` | prints `VERSION` |

`sdlc.py` imports each module inside its handler. A missing or unfinished module breaks only its own command. Exit 0 on success. On failure, `sdlc.py` prints one line that starts with `error: ` and exits non-zero. A file that is not UTF-8 text gives one such line. Ctrl-C gives one such line and exit code 130. When the reader closes stdout, for example `| head`, the command sends the rest of stdout to `os.devnull` and exits 1 with no message. `--dry-run` prints the argv and the env key names, never env values. It does not read the bot token file.

The `--stage-dir` default is the skill folder that holds the engine as `core/`, from the path that started `sdlc.py` (the symlink name stays). When the engine is not in a `core/` folder of a skill, for example the engine copy in `sdlc/.engine/`, a command that needs the stage folder asks for `--stage-dir`. A relative `--input` path is relative to the current folder. `role` gives it to the role process relative to the repo when the file is in the repo, else as an absolute path.

## 13. github, gates, setup_pr (S0b)

`github` holds every GitHub call. It calls `gh api` and `git` through one function, `github._run`, so tests can replace it.

| Signature | Behavior |
|---|---|
| `parse_remote(url) -> (owner, name)` | Only an HTTPS `github.com` URL. Else `SdlcError` |
| `origin_slug(repo) -> "owner/name"` | From the `origin` remote |
| `bot_login(config) -> str` | Config key `bot.login`. Else `SdlcError` |
| `bot_env(config, base_env=None) -> dict` | The env of `role.build_invocation` with no human token, plus `GH_TOKEN` and `OPIEWORKS_SDLC_BOT_SECRET` set to the bot token, an empty `GH_CONFIG_DIR`, and `GIT_CONFIG_*` entries that reset `credential.helper`, then add a helper that reads the token from the env. The token is never in argv. The caller calls `cleanup(env)` |
| `api(method, path, body=None, env=None) -> object` | `gh api`. The body goes on stdin as JSON. `env=None` uses the login of the person who runs the command. HTTP 404 raises `NotFound` (a `SdlcError`) |
| `push(repo, branch, config)` | `git push` as the bot. Refuses the default branch |
| `open_pr(repo, head, title, body, config, base=None, labels=()) -> url` | Opens a PR as the bot. For each label, `ensure_label` makes it when it is absent. Then one call adds all labels to the PR. All calls use the bot env. An empty label raises `SdlcError` before the PR opens |
| `ensure_label(slug, name, env)` | Makes the label with `LABEL_COLOR` and the text in `LABEL_DESCRIPTIONS` when the repo does not have it |

The engine has no merge, approve, or deployment-approval call (FR-10).

`setup_pr.open_setup_pr(stage_dir, repo) -> SetupReport`: all checks first (bot login, bot token file, HTTPS origin, a clean tree, no branch `sdlc/setup/<stage>`). Then make that branch, run `setup`, commit, push as the bot, and open the PR as the bot with the current branch as the base. With no changes, delete the branch and return `pr_url` None. The user ends on the start branch with a clean tree. A failure before the push deletes the branch.

`gates` uses the human login, because only a repo admin can change or fully read the rules.

- `ruleset_payload()`: ruleset `opieworks-sdlc` on `~DEFAULT_BRANCH`, active. The only bypass actor is the repository admin role (`bypass_mode` always). Rules: `pull_request` with 1 approval, code-owner review, dismiss stale reviews on push, approval of the last push. `required_status_checks` with `sdlc-checks`. `update` with `update_allows_fetch_and_merge` false (C5: the bot cannot merge after an approval; the admin merges through the bypass).
- `apply(slug, config) -> list[str]`: refuses until the default branch has `.github/workflows/sdlc-checks.yml` (FR-93). Creates or updates the ruleset by name. Sets environment `production` with the user in `codeowners.owner` as the required reviewer, self-review prevented, and custom branch policies. Makes the default branch the only deployment branch policy. Refuses when `codeowners.owner` is missing, a team, or the bot.
- `check_bot(slug, config) -> list[RuleResult]`: one result per rule: `codeowners-present`, `bot-not-code-owner`, `bot-no-admin-rights` (role read, triage, or write), `pr-required`, `code-owner-review`, `dismiss-stale-approvals`, `approve-latest-push`, `required-check`, `no-bot-bypass` (only the admin role may bypass), `restrict-updates` (an `update` rule is on), `env-human-reviewer`, `env-prevent-self-review`, `env-main-only`. Branch rules come from the effective rules of the default branch.

FR-93: every stage installs `core/templates/sdlc-checks.yml` as `.github/workflows/sdlc-checks.yml` (managed) and the core CODEOWNERS paths. The job runs on `pull_request_target`, checks out the base commit, and runs `python3 sdlc/.engine/sdlc.py checks`.

`tests/live/scn5_gate_integrity.py` is the live SCN-5 and C5 test. unittest does not run it.

## 14. verify (S1)

```python
CONFIG_TABLE = "testing"   # keys: verify (str), max_runs (int >= 1)
def settings(config) -> tuple[str, int]
def run_verify(command: str, repo: Path, max_runs: int, runner=None) -> VerifyResult
def write_evidence(item_id, repo, stage_dir, result, links=None, fallback=None) -> Path
def propose(repo) -> list[tuple[str, str]]   # (command, reason), best first
```

- `run_verify` runs the command in a shell in the repo, with stdout and stderr together. It stops at the first exit code 0, else after `max_runs` runs. `VerifyResult.verdict` is `PASS`, `PASS AFTER RETRY`, or `FAIL`. An empty command raises `SdlcError`.
- `write_evidence` fills `templates/evidence.md` (overlay first) and writes `sdlc/work/<id>/evidence.md`. It makes the work item folder when it is absent. Placeholders: `work_item`, `upstream`, `verify_command`, `max_runs`, `run_count`, `verdict`, `runs`, `report_pending`. An unknown placeholder raises `SdlcError`. The raw output of each run goes in a fenced block whose fence is longer than any backtick run in the output (NFR-7).
- `upstream`: the `links` when given, else the names of `intent.md`, `spec.md`, and `plan.md` in the folder, else `none, used <fallback>` with the default fallback `PR description and diff` (FR-3).
- `propose` reads repo files only. It runs nothing.

## 15. evals (S1)

```python
TASKS_REL = "sdlc/evals/tasks"
RESULT_REL = "sdlc/evals/results/latest.json"
def is_watched(path: str) -> bool
def watch_hash(entries) -> str          # entries: (mode, path, blob sha)
class GitSource(repo); class ApiSource(slug, env=None)   # blobs(rev), read(rev, path), merge_base(a, b)
def eval_gate(source, base, head) -> GateResult           # .ok, .lines
def load_tasks(repo, names=None) -> list[Task]
def installed_stages(repo) -> list[str]                  # stage IDs with a section in AGENTS.md
def resolve_role(name, stage_dir, repo) -> Path
def run_evals(repo, stage_dir, harness, names=None, now=None) -> tuple[dict, Path]
```

- Watched paths: every file named `AGENTS.md`, and every path under `sdlc/` except `sdlc/work/`, `sdlc/triage/`, `sdlc/lessons/`, and `sdlc/evals/results/`.
- `watch_hash` is the SHA-256 of the sorted lines `<mode> <blob sha>\t<path>` of the watched blobs. Git and the GitHub tree API give the same blob IDs, so a local hash and a CI hash match.
- A source reads trees and blobs only. It never checks out or runs a commit (FR-93).
- `eval_gate`: the PR changes a watched path when the watched blobs differ between the merge base and the head. No change: PASS. A change and no result on the base: PASS with a notice. A change and no result on the head, a result that is not valid, a hash that does not match the head, or a pass rate below the base result: FAIL.
- A task file has `role` and `check` (required), and `prompt`, `inputs`, `setup` (optional). Other keys raise `SdlcError`.
- `resolve_role` tries `agents/<name>.md` in the overlay, then in `stage_dir`, then in each folder next to `stage_dir` whose `stage.toml` ID is in `installed_stages` of the worktree. The first file wins. With no file, it raises `SdlcError` that names the folders it tried. So `run-evals` from one stage runs the eval tasks of every installed stage.
- `run_evals` refuses uncommitted changes in watched paths. It runs each task in a temporary detached worktree of `HEAD`: the `setup` command, then the role from `resolve_role` through `role.build_invocation` with the task `prompt` as `task` and stdin closed, then the `check` command with `EVAL_OUTPUT` (the file with the role output) and `EVAL_ROLE_EXIT`. A task passes when the role and the check exit with 0. The result holds `format`, `harness`, `head`, `watch_hash`, `created`, `tasks_total`, `tasks_passed`, `pass_rate`, and per task the raw `setup_log`, `role_log`, and `check_log`.

## Stage: intent

The intent stage (slice S2) adds `sdlc_lib/intent.py` and the `intent` CLI group. It changes one shared value: `evals.WATCH_EXCLUDE` also holds `sdlc/triage/`, because a triage record is not an agent instruction (section 15).

```python
STAGE = "intent"
LABEL = "sdlc:intent"
KINDS = ("bug", "feature")
SOURCES = {"text": "free text", "thread": "thread", "issue": "issue"}
CHANGE_KEYS = ("api_change", "token_change", "visual_change")   # values: yes, no, unknown
DEFAULT_SECTIONS = ("Problem", "Outcome", "Affected users and systems", "Constraints", "Open questions", "Lane", "Done when")
TRIAGE_REL = "sdlc/triage"   # intent records: sdlc/triage/intent-<id>.toml
STATES = ("open", "accepted", "rejected", "rejected, no reason")   # OPEN, ACCEPTED, REJECTED, REJECTED_NO_REASON

def propose_lane(kind, changes) -> tuple[str, str]
def lane_problem(kind, lane, changes) -> str | None
def new_intent(title, kind, source, repo, stage_dir, changes=None, links=None, today=None) -> tuple[str, str, str]
def check_intent(item_id, repo, config) -> list[str]
def set_lane(item_id, repo, lane) -> bool
def issue_text(slug, number, env=None) -> tuple[str, str]
def open_intent_pr(repo, item_id, config) -> str
def list_intent_pulls(slug, env=None, max_pages=20) -> list[dict]
def read_triage(repo, ref="HEAD") -> dict[str, dict]
def read_lanes(repo, ref="HEAD") -> dict[str, str]
def intent_states(pulls, triage, lanes=None) -> list[IntentState]
def find_pull(pulls, item_id) -> dict | None
def record_reason(repo, item_id, reason, pull, today=None) -> Path
```

- Lane rule (FR-11): `fast` only for kind `bug` with all three changes `no`. `unknown` counts as a change. `full` is always allowed. `set_lane` and `check_intent` refuse a fast lane that breaks the rule.
- `new_intent` fills `templates/intent-<kind>.md` (overlay first, FR-21) before it makes the work item, so a bad template makes no folder. Placeholders: `work_item`, `upstream`, `kind`, `source`, `lane`, `lane_reason`, `api_change`, `token_change`, `visual_change`, `title`. The lane in the frontmatter and in `item.toml` is the engine proposal. With no link, `upstream` is `none, used <source>` (FR-3). A source `issue` needs a link.
- `check_intent` lists problems: frontmatter keys, the lane rule, a lane that differs from `item.toml`, each section of `[intent].sections` (default `DEFAULT_SECTIONS`) missing or empty, and every line with `FILL:`.
- `open_intent_pr` needs the current branch `sdlc/<id>/intent` and a complete intent. It calls `github.open_pr` with `labels=[LABEL]`. The shared label code makes the label when it is absent and adds it to the PR.
- `intent_states` reads PR dicts of the pulls API (`number`, `state`, `merged_at`, `html_url`, `head.ref`, `labels`). Only a PR with the label and a branch `sdlc/<id>/intent` counts. The newest PR of an item wins. Merged gives `accepted`. Open gives `open`. Closed and not merged gives `rejected` with the reason of the triage record, else `rejected, no reason` (SCN-10). It never reads the author.
- `read_triage` and `read_lanes` read git objects at a ref with `ls-tree` and `show`. They never check out the ref.
- `record_reason` refuses an empty reason, no PR, an open PR, and a merged PR. It writes `work_item`, `stage`, `decision`, `reason`, `pr`, `closed_at`, and `recorded`. The engine has no call that closes or merges a PR (FR-10).

| Command | Calls |
|---|---|
| `intent new <title> --kind bug\|feature --source text\|thread\|issue [--upstream LINK ...] [--api-change V] [--token-change V] [--visual-change V] [--stage-dir] [--repo]` | `intent.new_intent`. Prints the ID, the proposed lane, and the path |
| `intent issue <number> [--repo]` | `intent.issue_text` with the human login. Read only |
| `intent check <id> [--repo]` | `intent.check_intent`. Exit 1 on any problem |
| `intent lane <id> fast\|full [--repo]` | `intent.set_lane` |
| `intent pr <id> [--repo]` | `intent.open_intent_pr`. Prints the URL |
| `intent status [<id>] [--ref REF] [--repo]` | `list_intent_pulls` with the human login, `read_triage`, `read_lanes`, `intent_states`. Read only |
| `intent record-reason <id> --reason TEXT [--repo]` | `find_pull`, `record_reason` |

## Stage: build

`sdlc_lib/build.py` (S3). The `checks` command calls `build.test_lock` in place of the old test-lock seam. That is the only change to the `checks` path.

```python
CONFIG_TABLE = "build"   # keys: test (str), api_paths, token_paths, visual_paths (lists of fnmatch patterns)
STATUSES = ("draft", "accepted")
BREACH_KINDS = ("none", "api", "token", "visual")
PHASES = ("plan", "test", "code")
BREACH_EXIT = 4
def parse_plan(text, where="plan.md") -> Plan          # work_item, lane, status, breach, breach_reason, locked_tests, body
def init_plan(item_id, repo, stage_dir, lane=None, links=None, fallback=None, request=None) -> Path
def build_check(item_id, repo, phase, config, base=None, runner=None) -> CheckResult   # .code, .lines
def test_lock(source, base, head) -> evals.GateResult
```

- plan.md frontmatter: `work_item`, `upstream`, `stage: build`, `lane`, `status`, `breach`, `breach_reason`, `locked_tests` (a list of safe repo-relative file paths). A bad value raises `SdlcError`.
- `init_plan` fills `templates/plan.md` (overlay first). Placeholders: `work_item`, `upstream`, `lane`, `request`, `planner_pending`, `acceptance_pending`. The lane comes from the argument, else `item.toml`, else `intent.md`. Upstream links are the given links, else `intent.md` and `spec.md` in the folder, else `none, used <fallback>` with the default fallback `user request` (FR-44). With no upstream, `request` is required. It refuses an existing plan.md.
- `build_check`: every phase checks plan.md. A fast-lane plan with a breach other than `none` returns `BREACH_EXIT` and a message that says to push nothing and names `opieworks-sdlc-design` (FR-11, SCN-4). Else the planner PENDING line and a fast-lane plan with no locked test are problems. `test`: the locked tests exist, and the `[build].test` command exits non-zero. `code`: acceptance (fast: `status: accepted` and no acceptance PENDING line, full: plan.md exists on `base`), a fast-lane change to a path that matches a `*_paths` pattern returns `BREACH_EXIT`, and each changed path outside `sdlc/work/` that plan.md does not name as a whole path is drift (FR-42). Changed paths are the diff from the merge base with `base` to the work tree, plus untracked files. Exit codes: 0 pass, 1 problems, 4 breach.
- `test_lock` (FR-41): lists the PR commits from the merge base to `head`, parents first (git `rev-list`, or the GitHub compare API, which must return every commit). A `plan.md` under `sdlc/work/<id>/` that differs from the merge base in a commit locks each `locked_tests` path that the commit tree has, as (mode, blob sha). A commit inherits the locks of all its parents in the PR, so the lock set is the union across all PR commits and a removed entry or plan does not unlock. A commit whose tree has another mode or blob, or no file, for a lock fails, once per lock. A locked path that no PR commit has fails. In the fast lane, a lock must start in the first commit that changes its plan, and a fast-lane plan at the head must lock a test. A plan that the PR does not change locks nothing. It reads trees and blobs only, never a checkout or a run (FR-93). An `SdlcError` becomes a FAIL line.

CLI (one block in `sdlc.py`, marked "Stage: build"):

| Command | Calls |
|---|---|
| `plan-init <id> [--lane fast\|full] [--request TEXT] [--upstream LINK ...] [--fallback TEXT] [--stage-dir] [--repo]` | `build.init_plan` |
| `build-check <id> --phase plan\|test\|code [--base REF] [--repo]` | `build.build_check` with `common.load_config`. Exit 4 on a fast-lane breach. `--base` default: `origin/HEAD`, else `main` |

## Stage: design

The design stage (S4) adds the module `design` and three CLI commands. It changes no other module.

```python
CONFIG_TABLE = "design"   # key: policies (list of repo-relative file paths)
SPEC_TEMPLATE = "templates/spec.md"
FALLBACK = "inline problem statement"

@dataclass
class Flag:
    id: str            # "FLAG-<n>"
    text: str
    resolution: str    # "" or "open" means that the flag is open
    open: bool         # property

def policy_list(config) -> list[str]
def policy_paths(repo, config) -> list[str]   # repo-relative POSIX strings
def draft_body(draft: str) -> str
def parse_flags(body: str) -> list[Flag]
def check_policy_section(body: str, policies: list[str]) -> None
def write_spec(item_id, repo, stage_dir, draft, config, problem=None, links=None) -> tuple[Path, list[Flag]]
def check_flag_history(old: list[Flag], new: list[Flag]) -> None
def check_spec(item_id, repo, config) -> list[Flag]
```

- `policy_paths` raises `SdlcError` when a configured file is missing, or when a path is not safe. An absent `[design]` table gives an empty list.
- Sections are level-2 headings (`## <name>`) outside a code fence. `draft_body` drops the text before the first one. A draft with none raises `SdlcError`.
- `parse_flags` (FR-31): the body has exactly one `## Flags` section. The section holds the line `None.`, or one or more lines `- FLAG-<n>: <text>`. Indented lines continue the last flag. An indented `Resolution: <text>` line sets its resolution. Any other line, both `None.` and a flag, no content, or a repeated flag ID raises `SdlcError`.
- `check_policy_section` (FR-30): the body has exactly one `## Policy check` section, and it names each configured policy path as text.
- `write_spec`: all checks run before the write. Upstream (FR-3, FR-6, FR-34): with `problem`, the work item must have no `intent.md` and no `links`. The frontmatter value is `none, used inline problem statement`, and the source section quotes the problem. Else the `links`, else `intent.md` when it exists. Else `SdlcError`. The template (overlay first) has the placeholders `work_item`, `upstream`, `policies`, `source`, and `body`. An unknown placeholder raises `SdlcError`. When `spec.md` exists, `write_spec` reads its flags and calls `check_flag_history`. The write uses `write_if_changed` (NFR-3).
- `check_flag_history` (FR-31, CON-4): it raises `SdlcError` when the new draft drops a flag ID of the earlier spec, or when a draft flag has a resolution that is not open and is not the human resolution of the earlier spec, word for word. With no earlier spec, a draft flag that is not open raises `SdlcError`.
- `check_spec` reads `sdlc/work/<id>/spec.md`. It raises `SdlcError` when the file is missing, when `work_item` is not the ID, when `upstream` is absent, or when a section check fails.

| Command | Calls |
|---|---|
| `policies [--repo]` | `design.policy_paths`. Prints one path per line, or a notice when none is configured |
| `spec <id> --draft FILE [--problem TEXT \| --problem-file FILE] [--upstream LINK ...] [--stage-dir] [--repo]` | `design.write_spec`. Prints the flag count and each open flag. Exit 0 also with open flags |
| `spec-check <id> [--repo]` | `design.check_spec`. Exit 1 while a flag is open |

## Stage: deploy

Module `sdlc_lib/deploy.py` (S5, FR-60 to FR-64). The roles do no GitHub call. The engine does each GitHub call for them as the bot, through `github.bot_env` and `github.api`. The module has no approve, merge, or deployment-approval call (FR-10).

```python
CONFIG_TABLE = "deploy"   # keys: nit_cap (int >= 0), babysit_rounds (int >= 1), docs_dir (str), docs_build (str)
def parse_findings(text) -> Review                 # .verdict, .findings, .blocked, .not_reported
def rank(findings, nit_cap) -> tuple[list[Finding], int]
def write_review(item_id, repo, stage_dir, review, nit_cap, links=None, fallback=None) -> Path
def comment_body(review_text) -> str
def post_comment(slug, pr, body, config, dry_run=False) -> str
def babysit(repo, stage_dir, pr_number, harness, config, dry_run=False) -> BabysitResult
def canary_payload(item_id, tag, target) -> dict
def canary(repo, item_id, tag, target, config, dry_run=False) -> str
def production(repo, source, item_id, config, dry_run=False, sleep=time.sleep, polls=10) -> ProductionRun
def history(repo, config, limit=10, dry_run=False) -> list[dict]
def rollback_source(rows) -> tuple[str, str]
def build_docs(repo, config, source, out) -> dict
```

- Reviewer report format: a `Verdict:` line (`FINDINGS`, `NO FINDINGS`, or `BLOCKED`), then lines `<n>. <important|nit> | <bug|security|compliance> | <location> | <title>`, each with indented detail lines, then `Not reported:` and, for `BLOCKED`, `Blocked:`. `parse_findings` reads from the last `Verdict:` line and skips fence lines, so harness text before the report does no harm. A verdict that does not match the finding lines raises `SdlcError`.
- `rank`: important findings first, then nits, each in the order of the reviewer. Only the first `nit_cap` nits stay. The second value is the number of hidden nits.
- `write_review` fills `templates/review.md` (overlay first) and writes `sdlc/work/<id>/review.md`. Placeholders: `work_item`, `upstream`, `verdict`, `marker`, `counts`, `not_reported`, `findings`. `upstream` links `intent.md`, `spec.md`, `plan.md`, and `evidence.md` when they exist, else `none, used <fallback>` with the default `PR description` (FR-3).
- `post_comment` posts an issue comment (`POST repos/<slug>/issues/<pr>/comments`) as the bot. An issue comment cannot approve. A dry run makes no call and does not read the bot token file.
- `babysit`: one round. It refuses a closed PR, a PR from a fork, a local branch that is not the PR head branch, uncommitted changes, and a local HEAD that is not the PR head. The round count is the number of bot comments with `BABYSIT_MARKER`. At the limit (`deploy.babysit_rounds`) it returns `handoff` and does nothing more. With no comment and no failed check it returns `handoff`. Else it writes the PR description, the comments, the reviews, and the failed check runs to a temp file, runs `agents/babysitter.md` (capability `edit`, no bot token) through `role.build_invocation` and `role.execute`, commits the changes, pushes the branch with `github.push`, and posts a comment with `BABYSIT_MARKER` that asks `codeowners.owner` to approve again. After the last round, `handoff` is True. A role that makes no change gives `handoff` and no push. A dry run reads with the human login and does not run the role, push, or post.
- `canary_payload` always has `prerelease: true`, `draft: false`, and `make_latest: "false"`. `canary` posts it as the bot and raises `SdlcError` when the answer has no prerelease flag.
- `production` reads the default branch as the bot, resolves the source to a full commit SHA (default: the head of the default branch), and dispatches `PRODUCTION_WORKFLOW` on the default branch with the inputs `source_sha` and `work_item`. It has no parameter for another branch. It finds the new run and returns its URL. It never approves the deployment.
- `history` lists the runs of `PRODUCTION_WORKFLOW`. The source commit comes from the run name `production <sha> (<work item>)`. `rollback_source` gives the source of the newest successful run and of the newest earlier successful run with a different source. It raises `SdlcError` when there is none.
- `build_docs` runs in the production job. It refuses a source that is not in the history of HEAD (`git merge-base --is-ancestor`), so a rollback also deploys only merged commits. It builds in a temporary detached worktree of the source: it runs `deploy.docs_build` when it is not empty, then copies `deploy.docs_dir` to `out` and writes `out/sdlc-build.json` with the source commit. It removes the worktree.

CLI block (one contiguous block in `sdlc.py`, marked "Stage: deploy"):

| Command | Calls |
|---|---|
| `deploy review-post <id> --findings FILE [--pr N] [--upstream L ...] [--fallback T] [--dry-run] [--stage-dir] [--repo]` | `parse_findings`, `write_review`, and with `--pr` `post_comment` |
| `deploy babysit <pr> --harness <h> [--dry-run] [--stage-dir] [--repo]` | `babysit`. Prints one line per step. A `handoff:` line means that the babysitter stops |
| `deploy canary <id> --tag T [--target REF] [--dry-run] [--repo]` | `canary` |
| `deploy production [--source REF] [--item ID] [--dry-run] [--repo]` | `production` |
| `deploy history [--limit N] [--repo]` | `history` |
| `deploy rollback [--to SHA] [--item ID] [--dry-run] [--repo]` | `history`, `rollback_source`, `production` |
| `deploy build-docs --source SHA --out DIR [--repo]` | `build_docs`. CI only |

The stage installs `github/workflows/sdlc-deploy-production.yml` (managed). It has only a `workflow_dispatch` trigger and one job, bound to the environment `production` that `gates apply` protects. It checks out the dispatched commit with the full history and runs `deploy build-docs` from `sdlc/.engine/`.

## Stage: maintain (S6)

`sdlc_lib/maintain.py` holds the engine of the maintain stage. `detect` is deterministic (NFR-4). It never starts a role or a model. Its only network calls are the issue writes, and only with `--issues`.

```python
CONFIG_TABLE = "maintain"   # keys: window (int >= 2). C3: 30 readings
BANDS_REL = "sdlc/bands.toml"
DATA_BRANCH = "sdlc-data"
READINGS_DIR = "sdlc/maintain/readings"       # on DATA_BRANCH: <metric>.csv
LAST_RUN_REL = "sdlc/maintain/last-run.json"  # on DATA_BRANCH
TRIAGE_REL = "sdlc/triage"; LESSONS_REL = "sdlc/lessons"
TIERS = ("ok", "log", "diagnose", "propose"); WARMUP = "warmup"; ERROR = "error"
DEFAULT_SIGMAS = {"log": 1.0, "diagnose": 2.0, "propose": 3.0}   # playbook
CSV_HEADER = ("time", "value", "mean", "sigma", "z", "tier")
ISSUE_LABEL = "sdlc:maintain"
DECISIONS = ("fix-now", "schedule", "dismiss")

def load_bands(repo) -> list[Metric]
def window_size(config) -> int
def evaluate(value, history: list[float], metric, window) -> Reading
def detect(repo, metrics, window, now=None, runner=None, record=True) -> DetectReport
def file_issues(slug, report, env=None, api=None) -> list[str]
def last_run(repo) -> dict | None
def readings_text(repo, metric) -> str
def new_finding(repo, stage_dir, metric, issue=None, lane="full", today=None) -> tuple[str, Path]
def triage(repo, item_id, decision, reason=None, issue=None, now=None) -> Path
def new_lesson(repo, stage_dir, item_id, pr=None, today=None) -> Path
def close_check(repo, item_id) -> list[str]
```

- `sdlc/bands.toml`: one table `[metrics.<name>]` per metric. The name has lowercase letters, digits, and hyphens. Keys: `command` (required, prints one number on its last line), `baseline` and `sigma` (optional fixed values), `bad` (`high`, `low`, or `both`, default `both`), `tiers` (optional `log`, `diagnose`, `propose` limits in sigma units, more than 0 and in that order), `description`. Other keys raise `SdlcError`. A missing file gives no metrics.
- `evaluate`: a part of the baseline that the metric does not fix comes from the last `window` readings of history: `statistics.fmean` and the sample `statistics.stdev`. When that part is needed and history has fewer than `window` readings, the tier is `warmup`. `z = (value - mean) / sigma`. With sigma 0, z is 0 for the same value, else infinite. The deviation is `abs(z)`, or only the positive side for `bad = "high"`, or only the negative side for `bad = "low"`. The tier is the highest limit that the deviation reaches.
- `detect` runs each command in a shell in the repo. A non-zero exit or a last line that is not a finite number gives the tier `error` for that metric, and the other metrics still run. It appends one row per metric to `<metric>.csv` and writes `last-run.json` (`format`, `time`, `window`, and per metric `value`, `tier`, `z`, `command`, `error`). It commits with git plumbing (a temporary index, `commit-tree`, `update-ref`) on the local branch `sdlc-data`. It builds on the local branch, else on `origin/sdlc-data`, else it makes a root commit. It never changes the work tree, the index, or HEAD. It does not fetch or push. The commit date is the run time, so the same series and clock give the same commit. Numbers in the CSV have at most 6 decimals (`fmt`).
- `file_issues`: for each result at `diagnose` or `propose`, it finds the open issue with the label `sdlc:maintain` and the title `sdlc detect: <metric> at <tier>`. It comments on that issue, else it opens a new issue with the label. It reads every page of open issues and skips pull requests.
- `last_run` is the seam for the hub warning (FR-84). It returns the parsed `last-run.json` from `sdlc-data`, or None. Its `time` is UTC ISO 8601 with a `Z`.
- `new_finding` makes a work item with `workitem.new_item("<metric> <tier> breach", lane)` and writes `sdlc/work/<id>/intent.md` from `templates/intent.md` (overlay first). The stage needs no intent stage (D7). With an issue, `upstream` lists `issue #<n>` and the readings path. With no issue, it is `none, used the command that prints the number, <command>` (FR-3).
- `triage` adds one entry `## <time>: <decision>` with the issue and the reason to `sdlc/triage/<id>.md`. It makes the file with frontmatter when it is absent. Old entries stay. A dismiss with no reason raises `SdlcError`.
- `new_lesson` writes `sdlc/lessons/<id>.md` from `templates/lesson.md` and refuses when it exists. `close_check` lists the problems: a missing lesson, a lesson with a `PENDING:` line, a missing `sdlc/evals/tasks/<id>.toml`, or a task that `evals.load_tasks` rejects.

CLI (one block in `sdlc.py`, marked "Stage: maintain"):

| Command | Calls |
|---|---|
| `detect [--metric NAME ...] [--command CMD] [--issues] [--dry-run] [--repo]` | `select_metrics`, `detect`, then `file_issues` with `--issues`. In GitHub Actions (`GITHUB_ACTIONS=true`) the issues use the job token and `GITHUB_REPOSITORY`. Else they use `github.bot_env`. Exit 1 when a metric has the tier `error` |
| `readings <metric> [--repo]` | `readings_text` |
| `last-run [--repo]` | `last_run`. Prints the time. Exit 1 when no run is recorded |
| `finding <metric> [--issue N] [--lane fast\|full] [--stage-dir] [--repo]` | `new_finding`. Prints the ID, then the path |
| `triage <id> --decision fix-now\|schedule\|dismiss [--reason T] [--issue N] [--repo]` | `triage` |
| `lesson <id> [--pr N] [--stage-dir] [--repo]` | `new_lesson` |
| `close-check <id> [--repo]` | `close_check`. Exit 1 on any problem |

The workflow `sdlc-detect.yml` runs only on `workflow_dispatch` (D24, no schedule) with `contents: write` and `issues: write`. It fetches `sdlc-data` when the branch exists, runs `python3 sdlc/.engine/sdlc.py detect --issues`, and pushes `sdlc-data`. It has no model step (D15).

## Stage: hub (S7)

Module `sdlc_lib/hub.py` (FR-80 to FR-84). The hub reads only. It makes `GET` calls with the login of the person who runs it (`github.api` with no env), and git reads with `git show` and `git ls-tree`. It never writes a file, pushes, comments, or uses the bot token. No stage imports it (FR-83).

```python
STAGES = ("intent", "design", "build", "testing", "deploy", "maintain")
STALE_DAYS = 50
def find_stages(hub_dir, bundles=None) -> dict[str, Path]
def snapshot(repo, api=None, ref=None, now=None) -> Snapshot
def item_ids(snap) -> list[str]
def item_state(item, snap) -> ItemState
def find_item(item, snap) -> ItemState
def status(snap) -> dict
def render_status(data) -> list[str]
def next_step(state, stages) -> dict
def render_next(data) -> list[str]
```

- `find_stages`: the given bundle folders first, then `opieworks-sdlc-<stage>` next to `hub_dir`. A folder counts when `common.load_stage` loads its `stage.toml` and the ID is one of `STAGES`. The first folder for an ID wins.
- `snapshot`: the default branch comes from `repos/<slug>`. The ref is `--ref`, else `origin/<default>`, else `<default>`. It reads on that ref: `sdlc/config.toml` (`codeowners.owner`), the AGENTS.md sections (installed stages), every file in `sdlc/work/<id>/`, `sdlc/triage/<id>.md`, `sdlc/lessons/<id>.md`, and `intent.read_triage`. It reads from GitHub: every PR (`state=all`), every branch, the open issues with the label `sdlc:maintain`, and the runs of `sdlc-deploy-production.yml` (a 404 gives no runs). `maintain.last_run` gives the last detect run.
- Work item IDs come from work folders, triage and lesson files, the head branch `sdlc/<id>/<part>` of a PR or a branch, and the `(<id>)` suffix of a production run name. Never from the PR author. The branch parts are `intent`, `triage`, `design`, `plan`, `build`, `testing`, `deploy`, `maintain`.
- `item_state` checks from the last stage to the first, and the first match wins: a lesson (done), an open `maintain` PR, a `dismiss` triage entry, production runs (waiting, running, success, failure), a merged code PR (`build`, `testing`, or `deploy` part), an open code PR (`evidence.md` and `review.md` at the PR head through the contents API, the latest review of each user), a plan PR or `plan.md` on the ref (a fast-lane breach routes to design), a design PR (open flags of `spec.md` at the head through `design.parse_flags`) or `spec.md` on the ref, the intent gate (`intent.intent_states`), else "no intent yet". A fast-lane item after an accepted intent goes to build, a full-lane item goes to design (FR-11, FR-33). With no lane, the gate owner sets it.
- `ItemState` fields: `item`, `stage`, `lane`, `gate`, `pr`, `waiting_on` (`@<codeowners.owner>`, or a line that names the config key), `action`, `next_stage`, `next_step`, `command`. `command` and `action` hold `{skill}` for the stage folder. `next_step` replaces it with the folder from `find_stages`, else with `<opieworks-sdlc-<stage> folder>`.
- `status` warnings: the ref sha differs from the GitHub branch head. The last detect run is more than `STALE_DAYS` days old. No detect run while AGENTS.md has the maintain section (FR-84).

CLI (one block in `sdlc.py`, marked "Stage: hub"):

| Command | Calls |
|---|---|
| `status [--ref REF] [--json] [--repo]` | `snapshot`, `status`, `render_status` |
| `next <id> [--ref REF] [--json] [--bundle DIR ...] [--stage-dir DIR] [--repo]` | `snapshot`, `find_item`, `find_stages`, `next_step`, `render_next`. Exit 1 for an unknown work item |
