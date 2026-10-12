# Porting {{skill}}

This bundle is a standalone copy of the `{{skill}}` skill. It needs no other skill and no skill catalog. Read this file before you install the bundle on a new machine or in a new team.
{{if hub}}

This skill is the hub. The hub has no stage file. It installs nothing in a repo. Install the stage bundles that you need, and use the hub to work with them.
{{end}}
{{if scaffold}}

This skill is a scaffold. It is not built yet. Its `SKILL.md` tells the agent to stop and report that. Do not use the bundle for real work until a later version builds it.
{{end}}

## What the bundle holds

| Path | Content |
|---|---|
| `SKILL.md` | The procedure that the agent follows |
{{if stage}}
| `stage.toml` | The stage definition |
{{end}}
| `core/` | The `sdlc.py` engine. The export copies real files here |
{{if built}}
| `templates/` | Default artifact templates |
| `agents/` | Default role specs |
| `files/` | Default repo files, for example `REVIEW.md`, when the stage has them |
| `github/` | Default GitHub workflow files, when the stage has them |
{{end}}
| `PORTING.md` | This file |

## Requirements

- Python 3.11 or later. The engine stops with a clear message on an older Python. Some operating systems ship an older `python3`. Run `python3 --version` to check it.
- Git and a GitHub repo. The process supports GitHub only.
- The GitHub CLI `gh`, signed in as the human user.
- No third-party Python packages. The engine uses the standard library only.

## Install the bundle

1. Copy the bundle folder into the skills folder of your agent harness. Keep the folder name.
2. Run `python3 core/sdlc.py version` in the bundle folder. The command prints the engine version.
{{if stage}}
3. Go to the target repo. Run `python3 <bundle>/core/sdlc.py setup <bundle>`.
4. Read the output. It lists the files that changed and the new config keys.
5. Review the changes and merge them through a pull request.

A second `setup` with no changes writes nothing.
{{end}}
{{if stage}}
{{if scaffold}}

A scaffold stage has no install files yet. Its `setup` writes only the engine copy in `sdlc/.engine/` and `sdlc/config.toml`.
{{end}}
{{end}}
{{if hub}}

The hub has no stage file, so the `setup` command does not accept the hub folder. Run `setup` with each stage bundle that you need.
{{end}}
{{if stage}}

## Configure the repo

Setup writes `sdlc/config.toml` one time. After that, the file belongs to you. Setup never changes it. When a new version adds config keys, setup prints their names. Add them by hand.

Set these keys first:

- `codeowners.owner`: the GitHub user or team that holds the gates. Setup writes the placeholder `@OWNER` until you set it.
- `bot.token_file`: the path of the bot token file. The default is `~/.config/opieworks-sdlc/bot.env`.
- `bot.login`: the GitHub user name of the bot. `push`, `pr`, `setup --open-pr`, `gates apply`, and `check-bot` need it.
- `[agent_runner.<harness>]`: the command and the flags for each capability of your agent harness.

The `[agent_runner]` tables have this shape. Replace each `<...>` value with the command and flags of your harness:

```toml
[agent_runner]
default = "<harness>"

[agent_runner.<harness>]
command = ["<harness command>", "<flag that runs one prompt>"]
read    = ["<flags that allow file reads only>"]
run     = ["<flags that also allow shell commands>"]
edit    = ["<flags that also allow file edits>"]
```

The engine runs `command`, then the flags of the strongest capability of the role (`edit`, then `run`, then `read`), then the prompt as the last argument. A role with only `github-bot` gets the `read` flags. This engine version does not read `default`. Give `--harness <name>` on each `role` command. Check the flags against the documentation of your harness version. The Harness notes section has examples.

A later engine version adds the `[gates]` table. It will map each gate to a role, with the solo profile as the default. This engine version does not read `[gates]`.
{{end}}
{{if built}}

## Change the defaults with the overlay

The overlay is the `sdlc/templates/` and `sdlc/agents/` folders in the target repo. A file in the overlay replaces the bundle default with the same relative path. A `<relpath>` starts with `templates/` or `agents/`. Setup owns the other files under `sdlc/`.

Run these commands in the target repo. `<bundle>` is the path of the bundle folder. The engine uses the bundle that holds it as the stage folder. Use `--stage-dir <folder>` to name a different stage folder.

- Run `python3 <bundle>/core/sdlc.py resolve <relpath>` to see which file the engine uses.
- Run `python3 <bundle>/core/sdlc.py eject <relpath>` to copy a default into the overlay. Then edit the copy.

Setup never writes overlay files.

## Roles

A role spec is a markdown file in `agents/`. Its frontmatter has `name`, `stage`, `capabilities`, `fresh_context`, `inputs`, `outputs`, and `stop_when`. The capabilities are `read`, `run`, `edit`, and `github-bot`.

- In the target repo, run `python3 <bundle>/core/sdlc.py role <role> --harness <name> --dry-run` to see the command and the names of the environment variables. `<role>` is a role name, for example `reviewer`. The engine uses `sdlc/agents/<role>.md` from the overlay when it exists, else `<bundle>/agents/<role>.md`. A path to a spec file also works. A dry run does not read the bot token file.
- An `--input` path is relative to the current folder. The engine gives it to the role process relative to the repo.
- The engine gives the bot token to the role process only when the role has the `github-bot` capability.

The engine isolates each role process from the GitHub login of the human user. It does these things:

- It removes `GH_TOKEN`, `GITHUB_TOKEN`, `GH_ENTERPRISE_TOKEN`, and `GITHUB_ENTERPRISE_TOKEN`.
- It removes every variable whose name has a `GH` or `GITHUB` word and a `TOKEN` or `PAT` word, for example `GITHUB_PERSONAL_ACCESS_TOKEN` and `GITHUB_PAT_TOKEN`. It also removes every variable whose value has the shape of a GitHub token.
- It removes `GIT_ASKPASS`, `SSH_ASKPASS`, and every `VSCODE_GIT_*` variable. It sets `SSH_ASKPASS_REQUIRE=never`. Then an editor terminal cannot give the human login to `git` or `ssh` through an askpass program.
- It sets `GH_CONFIG_DIR` to a new empty temporary folder. Then `gh` cannot read the stored login of the human user from its config folder or from the system keyring. The engine removes the folder when the role stops.
- It sets `GH_PROMPT_DISABLED=1` and `GIT_TERMINAL_PROMPT=0`. Then `gh` and `git` do not ask for a login.
- It adds two git config entries through `GIT_CONFIG_COUNT`, `GIT_CONFIG_KEY_<n>`, and `GIT_CONFIG_VALUE_<n>`: an empty `credential.helper` and an empty `core.askPass`. It adds them after the `GIT_CONFIG_*` entries that the caller already has, and it keeps those entries. Then `git` cannot get the stored login of the human user from a helper or an askpass program.

The engine does not isolate other credentials, for example an SSH agent or SSH keys. A role process can push with an SSH key of the human user when the remote uses SSH. The engine also keeps `HOME`, so `git` over HTTPS can read a login from `~/.netrc`. Do not keep a GitHub login in `~/.netrc` on a machine that runs roles.
{{end}}
{{if stage}}

## Set up the bot

Agents act on GitHub only as a bot. The bot has no gate rights.

1. Make a machine account on GitHub for the bot.
2. Invite the bot to the target repo as a collaborator with Write access.
3. Make a classic token for the bot with the `repo` and `workflow` scopes. A fine-grained token does not work for a collaborator on a repo that another user owns.
4. Write the token to the bot token file as one line: `GH_TOKEN=<value>`.
5. Set the file mode to 0600. Run `chmod 600 ~/.config/opieworks-sdlc/bot.env`.
6. Do not make the bot a code owner. Do not give the bot admin rights.
7. Use an HTTPS `origin` remote. The bot credential works only on HTTPS.

Then do the bootstrap in this order:

1. Run `python3 <bundle>/core/sdlc.py setup <bundle> --open-pr`. The bot opens the setup PR.
2. A human reviews and merges the setup PR.
3. A repo admin runs `python3 sdlc/.engine/sdlc.py gates apply`. It makes the branch ruleset and the `production` environment rules.
4. A repo admin runs `python3 sdlc/.engine/sdlc.py check-bot`. Every rule must show PASS.

The ruleset lets only a repo admin update the default branch, through the admin bypass. Without this rule, the bot can merge a pull request after a human approves it. The result: only a repo admin can merge. On a team, give the admin role to each person who merges, or remove the `update` rule and accept that the bot can merge an approved pull request.

Never commit the token file. Never paste the token into a chat or a log.
{{end}}

## Accepted risk: the human token in the agent session

The agent session runs with the GitHub token of the human user. This token can approve and merge pull requests. An agent that ignores the procedure can approve or merge a pull request as the human user.
{{if built}}
The engine isolates role processes from the human login, as the Roles section tells. It cannot do this for the main agent session.
{{end}}

The procedures never approve, merge, or start a production release. The GitHub rules hold the important gates.

The bot has Write access, so GitHub lets it approve a pull request that it did not open. An agent can open a pull request with the human token and then approve it with the bot token. That approval counts for every file that CODEOWNERS does not cover. Only a code owner can approve files that CODEOWNERS covers. To close this path, add `* @<owner>` as the first CODEOWNERS rule. Then every pull request needs a code-owner approval. The human then needs an admin bypass to merge their own pull requests.

To lower the risk, add an optional deny rule to your agent harness. The rule blocks these actions:

- `gh pr review` in every form. The approve flag can come after the pull request number, and `-a` is a short form. The bot posts review comments, so the human session does not need this command.
- `gh pr merge`
- GitHub API calls to the pull request reviews endpoints (`repos/<owner>/<repo>/pulls/<number>/reviews`)
- GitHub API calls to the pull request merge endpoint (`repos/<owner>/<repo>/pulls/<number>/merge`)
- GitHub API calls to the deployments endpoints (`repos/<owner>/<repo>/deployments` and `repos/<owner>/<repo>/actions/runs/<id>/pending_deployments`)
- GitHub GraphQL calls with the `mergePullRequest`, `enablePullRequestAutoMerge`, or `addPullRequestReview` mutation (`gh api graphql`)

The human user then approves and merges in the GitHub web interface.

## Check a bundle for leaks

Run `python3 core/sdlc.py leak-scan <folder>` and `python3 core/sdlc.py lint-portable <folder>` before you share a bundle. Each command exits with a non-zero code on a hit.

Put private words, for example personal names and account names, in `~/.config/opieworks-sdlc/leak-denylist.txt`. Write one word on each line. The environment variable `OPIEWORKS_SDLC_LEAK_DENYLIST` can name a different file. Only the denylist finds personal names and account IDs. The `export` command refuses to run when the denylist is missing or empty. Use `export --no-denylist` only when you check the bundle for names in another way. The `leak-scan` command prints a notice when the denylist is missing or empty.

## Harness notes (optional)

These notes are examples. Check the documentation of your harness version.

### Claude Code

Add the deny rule to the `permissions.deny` list in `.claude/settings.json` in the target repo:

```json
{
  "permissions": {
    "deny": [
      "Bash(gh pr review:*)",
      "Bash(gh pr merge:*)",
      "Bash(gh api *pulls/*/reviews*)",
      "Bash(gh api *pulls/*/merge*)",
      "Bash(gh api *deployments*)",
      "Bash(gh api graphql:*)"
    ]
  }
}
```

A `Bash(x:*)` rule matches a command prefix. A prefix rule cannot see a GraphQL mutation name, so the example blocks `gh api graphql` as a whole. Remove that rule only when your agents need GraphQL and you accept the risk.
{{if stage}}

An example runner table for `sdlc/config.toml`:

```toml
[agent_runner]
default = "claude"

[agent_runner.claude]
command = ["claude", "-p", "--strict-mcp-config"]
read    = ["--tools=Read,Grep,Glob", "--allowedTools=Read,Grep,Glob"]
run     = ["--tools=Read,Grep,Glob,Bash", "--allowedTools=Read,Grep,Glob,Bash"]
edit    = ["--tools=Read,Grep,Glob,Edit,Write,Bash", "--allowedTools=Read,Grep,Glob,Edit,Write,Bash"]
```

Use the `=` form. `--tools` and `--allowedTools` take a list of values, so a flag value after a space also takes the prompt. `--tools` limits the tools. `--allowedTools` approves them for `-p` mode. `--allowedTools` alone does not limit the tools. `--strict-mcp-config` starts the role with no MCP server, because an MCP server can hold a login of the human user. The `run` map gives a shell, and a shell can write files.
{{end}}

### Codex

Use a command rule or a sandbox policy that blocks the same commands. The syntax depends on the Codex CLI version.
{{if stage}}

An example runner table for `sdlc/config.toml`. Check it against your Codex CLI version. It gives `run` and `edit` the same flags:

```toml
[agent_runner.codex]
command = ["codex", "exec"]
read    = ["-s", "read-only"]
run     = ["-s", "workspace-write"]
edit    = ["-s", "workspace-write"]
```
{{end}}
