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
| `load_config(repo, defaults: dict \| None = None) -> dict` | `CORE_DEFAULTS`, then `defaults`, then `sdlc/config.toml` | `SdlcError` on bad TOML |
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
7. Engine copy: make `sdlc/.engine/` match the core folder (the folder that holds `sdlc.py`) exactly, with `iter_files` and `EXCLUDED_DIRS`. Delete files in `sdlc/.engine/` that the core does not have and list them in `removed`. The names compare by exact case. The delete pass runs before the copy pass, so a case-insensitive disk cannot lose a file that only changed case. `VERSION` is part of the copy.
8. `sdlc/config.toml`: when absent, write `dump_toml(stage.config_defaults)` and set `config_created`. When present, never change it. Report the keys of `flatten_keys(config_defaults)` that `flatten_keys(existing)` lacks.
9. A second call with no changes writes nothing and returns an empty `written` and `removed`.
10. All checks run before the first write. Every destination goes through `safe_write_path`. Setup refuses `sdlc/.engine/` when it, a parent, or anything inside it is a symlink. The `config.toml` text is made before the first write.

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
def build_invocation(spec, harness: str, config: dict, inputs=(), base_env=None, dry_run=False) -> tuple[list[str], dict[str, str]]
def execute(argv: list[str], env: dict[str, str], cwd: Path) -> int
def cleanup(env: dict[str, str]) -> None
```

- `parse_role_spec` uses `common.parse_frontmatter`. All seven keys are required. `capabilities` is a non-empty list and a subset of `CAPABILITIES`. An unknown capability raises `SdlcError` that names it. `fresh_context` is a boolean.
- `read_bot_token` expands `~`. It reads the line `GH_TOKEN=<value>`. It skips blank lines and `#` lines. It raises `SdlcError` when the file or the line is missing. The message never holds the value.
- `build_invocation`:
  1. Read the table `config["agent_runner"][harness]`. Raise `SdlcError` when it is absent.
  2. `argv = command + flags + [prompt]`. `flags` is the map of the strongest of `edit`, `run`, `read` in the spec. A missing map for a needed capability raises `SdlcError`.
  3. `prompt` is the spec body, then a line `Inputs:`, then one line per input path.
  4. `env` is a copy of `base_env` (default: `os.environ`) plus `ISOLATION_ENV`. The copy drops the names in `HUMAN_TOKEN_VARS` and `ASKPASS_VARS`, every name that starts with `ASKPASS_PREFIX`, every name with a `GH` or `GITHUB` word and a `TOKEN` or `PAT` word, and every value with the shape of a GitHub token (the `token:github` leak patterns). Then the entries of `GIT_ISOLATION_CONFIG` go at index n and up, where n is the `GIT_CONFIG_COUNT` of the copy (0 when it is absent or not a number that is 0 or more). `GIT_CONFIG_COUNT` becomes n plus 2. The entries below n stay.
  5. Only when `github-bot` is in the capabilities: set `env["GH_TOKEN"]` to `read_bot_token(config["bot"]["token_file"])`. With `dry_run=True`, do not read the file. Set `env["GH_TOKEN"]` to `DRY_RUN_TOKEN`.
  6. Set `env["GH_CONFIG_DIR"]` to a new empty temp folder, so `gh` cannot find the stored human login. The caller removes it with `cleanup(env)`.
- `execute` runs `subprocess.run(argv, env=env, cwd=cwd)` and returns the exit code. A missing command raises `SdlcError`.

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
| `role <name-or-path> --harness <h> [--stage-dir] [--input FILE ...] [--repo] [--dry-run]` | `overlay.resolve("agents/<name>.md")` for a name, `role.parse_role_spec`, `common.load_config`, `role.build_invocation`, `role.execute`, `role.cleanup`. A value with a `/` or a `.md` suffix is a path |
| `leak-scan <dir>` | `scan.leak_scan`. Exit 1 on any hit. A notice on stderr when the denylist is missing or empty |
| `lint-portable <dir>` | `scan.lint_portable`. Exit 1 on any hit |
| `export <skill-dir> --out <dir> [--no-denylist]` | `export.export` |
| `push [--branch B] [--repo]` | `github.push` |
| `pr --title T [--body B \| --body-file F] [--head] [--base] [--repo]` | `github.open_pr`. Prints the URL |
| `gates apply [--repo]` | `gates.apply` |
| `check-bot [--repo]` | `gates.check_bot`. Prints one line per rule. Exit 1 on any FAIL |
| `checks` | The `sdlc-checks` CI job. No checks yet. Later slices add test-lock and eval-gate |
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
| `open_pr(repo, head, title, body, config, base=None) -> url` | Opens a PR as the bot |

The engine has no merge, approve, or deployment-approval call (FR-10).

`setup_pr.open_setup_pr(stage_dir, repo) -> SetupReport`: all checks first (bot login, bot token file, HTTPS origin, a clean tree, no branch `sdlc/setup/<stage>`). Then make that branch, run `setup`, commit, push as the bot, and open the PR as the bot with the current branch as the base. With no changes, delete the branch and return `pr_url` None. The user ends on the start branch with a clean tree. A failure before the push deletes the branch.

`gates` uses the human login, because only a repo admin can change or fully read the rules.

- `ruleset_payload()`: ruleset `opieworks-sdlc` on `~DEFAULT_BRANCH`, active, no bypass actors. Rules: `pull_request` with 1 approval, code-owner review, dismiss stale reviews on push, approval of the last push. `required_status_checks` with `sdlc-checks`.
- `apply(slug, config) -> list[str]`: refuses until the default branch has `.github/workflows/sdlc-checks.yml` (FR-93). Creates or updates the ruleset by name. Sets environment `production` with the user in `codeowners.owner` as the required reviewer, self-review prevented, and custom branch policies. Makes the default branch the only deployment branch policy. Refuses when `codeowners.owner` is missing, a team, or the bot.
- `check_bot(slug, config) -> list[RuleResult]`: one result per rule: `codeowners-present`, `bot-not-code-owner`, `bot-no-admin-rights` (role read, triage, or write), `pr-required`, `code-owner-review`, `dismiss-stale-approvals`, `approve-latest-push`, `required-check`, `no-bot-bypass` (only the admin role may bypass), `env-human-reviewer`, `env-prevent-self-review`, `env-main-only`. Branch rules come from the effective rules of the default branch.

FR-93: every stage installs `core/templates/sdlc-checks.yml` as `.github/workflows/sdlc-checks.yml` (managed) and the core CODEOWNERS paths. The job runs on `pull_request_target`, checks out the base commit, and runs `python3 sdlc/.engine/sdlc.py checks`.

`tests/live/scn5_gate_integrity.py` is the live SCN-5 and C5 test. unittest does not run it.
