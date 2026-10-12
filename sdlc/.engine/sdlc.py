#!/usr/bin/env python3
"""opieworks-sdlc engine entry point.

This file must parse on old Python versions. The version guard runs first.
Do not use syntax that is newer than Python 3.6 in this file. Import
tomllib and sdlc_lib modules only inside the command handlers.
"""

import sys

MIN_PYTHON = (3, 11)


def python_version_error(version_info):
    """Return the error line for a Python older than MIN_PYTHON, else None."""
    if tuple(version_info[:2]) >= MIN_PYTHON:
        return None
    return (
        "error: opieworks-sdlc needs Python 3.11 or later. "
        "This is Python {}.{}.{}. Install Python 3.11 or later and run the "
        "command again.\n".format(*version_info[:3])
    )


if python_version_error(sys.version_info):
    sys.stderr.write(python_version_error(sys.version_info))
    sys.exit(1)

# Do not write __pycache__ folders. A run of the engine copy in a target
# repo must leave the repo clean.
sys.dont_write_bytecode = True

import argparse  # noqa: E402
import os  # noqa: E402
import shlex  # noqa: E402
from pathlib import Path  # noqa: E402

ENGINE_DIR = Path(__file__).resolve().parent
if str(ENGINE_DIR) not in sys.path:
    sys.path.insert(0, str(ENGINE_DIR))

# A skill holds the engine as "core", a symlink in the canon and a real
# folder in a bundle. Keep the path as the caller gave it, so the symlink
# name stays.
ENGINE_FOLDER_NAME = "core"
CALLED_ENGINE_DIR = Path(os.path.abspath(__file__)).parent


def _repo(args):
    from sdlc_lib import common

    return common.repo_root(getattr(args, "repo", None))


def _stage_dir(args):
    """Return --stage-dir, else the skill folder that holds this engine as core/."""
    from sdlc_lib.common import SdlcError

    if args.stage_dir is not None:
        return Path(args.stage_dir)
    skill = CALLED_ENGINE_DIR.parent
    if CALLED_ENGINE_DIR.name == ENGINE_FOLDER_NAME and (skill / ENGINE_FOLDER_NAME / "sdlc.py").is_file():
        return skill
    raise SdlcError("This engine is not in the core folder of a skill. Use --stage-dir to name the stage folder.")


def cmd_version(args):
    print((ENGINE_DIR / "VERSION").read_text(encoding="utf-8").strip())
    return 0


def cmd_setup(args):
    from sdlc_lib import install

    report = install.setup(Path(args.stage_dir), _repo(args), open_pr=args.open_pr)
    for rel in report.written:
        print("wrote " + rel)
    for rel in report.removed:
        print("removed " + rel)
    for rel in report.skipped:
        print("kept " + rel)
    if report.config_created:
        print("created sdlc/config.toml")
    for key in report.new_config_keys:
        print("new config key: " + key)
    if not report.written and not report.removed and not report.config_created:
        print("no changes")
    if report.pr_url:
        print("opened setup PR " + report.pr_url)
    return 0


def _slug_config(args):
    from sdlc_lib import common, github

    repo = _repo(args)
    return repo, github.origin_slug(repo), common.load_config(repo)


def cmd_push(args):
    from sdlc_lib import github

    repo, _slug, config = _slug_config(args)
    branch = args.branch or github.git(repo, "symbolic-ref", "--short", "HEAD").strip()
    github.push(repo, branch, config)
    print("pushed " + branch + " as the bot")
    return 0


def cmd_pr(args):
    from sdlc_lib import github

    repo, _slug, config = _slug_config(args)
    head = args.head or github.git(repo, "symbolic-ref", "--short", "HEAD").strip()
    body = Path(args.body_file).read_text(encoding="utf-8") if args.body_file else (args.body or "")
    print(github.open_pr(repo, head, args.title, body, config, base=args.base))
    return 0


def cmd_gates_apply(args):
    from sdlc_lib import gates

    _repo_path, slug, config = _slug_config(args)
    for line in gates.apply(slug, config):
        print(line)
    return 0


def cmd_check_bot(args):
    from sdlc_lib import gates

    _repo_path, slug, config = _slug_config(args)
    results = gates.check_bot(slug, config)
    for result in results:
        print(result.format())
    failed = [r for r in results if not r.ok]
    print("check-bot: {} of {} rules pass".format(len(results) - len(failed), len(results)))
    return 1 if failed else 0


def _test_lock(source, base, head):
    """The test-lock check (FR-41). Slice S3 builds it. It finds nothing yet."""
    return ["test-lock: not installed yet."], True


def cmd_checks(args):
    """The sdlc-checks CI job. It runs from the base-branch engine copy (FR-93).

    It reads the PR head only as data: trees and blobs from git or the
    GitHub API. It never checks out or runs PR head code.
    """
    from sdlc_lib import evals

    if (args.base is None) != (args.head is None):
        from sdlc_lib.common import SdlcError

        raise SdlcError("Give both --base and --head, or neither.")
    if args.base is not None:
        found = (evals.GitSource(_repo(args)), args.base, args.head)
    elif os.environ.get("GITHUB_EVENT_PATH") and os.environ.get("GITHUB_REPOSITORY"):
        found = evals.event_source(os.environ["GITHUB_EVENT_PATH"], os.environ["GITHUB_REPOSITORY"])
    else:
        found = None
    if found is None:
        print("sdlc-checks: no pull request to check. Give --base and --head to check two commits.")
        return 0
    source, base, head = found
    ok = True
    for check in (_test_lock, lambda s, b, h: _eval_gate(evals, s, b, h)):
        lines, passed = check(source, base, head)
        for line in lines:
            print(line)
        ok = ok and passed
    print("sdlc-checks: " + ("PASS" if ok else "FAIL"))
    return 0 if ok else 1


def _eval_gate(evals, source, base, head):
    result = evals.eval_gate(source, base, head)
    return result.lines, result.ok


def cmd_verify(args):
    from sdlc_lib import common, verify

    repo = _repo(args)
    stage_dir = _stage_dir(args)
    config = common.load_config(repo)
    command, max_runs = verify.settings(config)
    if args.max_runs is not None:
        max_runs = args.max_runs
    verify.check_item_id(args.item_id)
    result = verify.run_verify(command, repo, max_runs)
    path = verify.write_evidence(args.item_id, repo, stage_dir, result, links=args.upstream, fallback=args.fallback)
    for attempt in result.attempts:
        print("run {} of {}: exit code {}".format(attempt.number, result.max_runs, attempt.exit_code))
    print("verdict: " + result.verdict)
    print("wrote " + path.relative_to(repo).as_posix())
    return 0 if result.passed else 1


def cmd_propose_verify(args):
    from sdlc_lib import verify

    found = verify.propose(_repo(args))
    if not found:
        print("No verify command found. Ask the human for the command that proves the repo works.")
        return 0
    for command, reason in found:
        print("{}    ({})".format(command, reason))
    return 0


def cmd_run_evals(args):
    from sdlc_lib import evals

    repo = _repo(args)
    result, path = evals.run_evals(repo, _stage_dir(args), args.harness, names=args.task or None)
    for task in result["tasks"]:
        print("{} {}".format("PASS" if task["passed"] else "FAIL", task["name"]))
    print("pass rate: {} of {}".format(result["tasks_passed"], result["tasks_total"]))
    print("wrote " + path.relative_to(repo).as_posix())
    return 0


def cmd_resolve(args):
    from sdlc_lib import overlay

    print(overlay.resolve(args.relpath, _stage_dir(args), _repo(args)))
    return 0


def cmd_eject(args):
    from sdlc_lib import overlay

    print(overlay.eject(args.relpath, _stage_dir(args), _repo(args)))
    return 0


def cmd_work_new(args):
    from sdlc_lib import workitem

    print(workitem.new_item(args.title, args.lane, _repo(args)))
    return 0


def cmd_work_show(args):
    from sdlc_lib import workitem

    repo = _repo(args)
    for art in workitem.show_item(args.item_id, repo):
        links = ", ".join(art.upstream) if art.upstream else "none"
        print("{}  upstream: {}".format(art.name, links))
    return 0


def _role_spec_path(spec, args, repo):
    """Return the spec file for a role name or a path.

    A value with no "/" and no ".md" suffix is a role name. It resolves
    agents/<name>.md from the overlay first, then from the stage folder.
    """
    from sdlc_lib import overlay

    if "/" in spec or spec.endswith(".md"):
        return Path(spec)
    return overlay.resolve("agents/{}.md".format(spec), _stage_dir(args), repo)


def _input_path(value, repo):
    """Return an --input path for the role process, which runs in the repo.

    A relative path is relative to the current folder of the caller. The
    result is relative to the repo when the file is in the repo, else absolute.
    """
    path = Path(os.path.normpath(os.path.join(os.getcwd(), os.path.expanduser(value))))
    try:
        return path.relative_to(repo).as_posix()
    except ValueError:
        return str(path)


def cmd_role(args):
    from sdlc_lib import common, role

    repo = _repo(args)
    spec = role.parse_role_spec(_role_spec_path(args.spec, args, repo))
    config = common.load_config(repo)
    inputs = [_input_path(item, repo) for item in args.input]
    argv, env = role.build_invocation(spec, args.harness, config, inputs=inputs, dry_run=args.dry_run, task=args.task)
    try:
        if args.dry_run:
            print("argv: " + " ".join(shlex.quote(a) for a in argv))
            print("env keys: " + " ".join(sorted(env)))
            return 0
        return role.execute(argv, env, repo)
    finally:
        role.cleanup(env)


def _report_hits(hits):
    for hit in hits:
        print(hit.format())
    return 1 if hits else 0


def cmd_leak_scan(args):
    from sdlc_lib import scan

    if not scan._default_denylist():
        sys.stderr.write(
            "notice: The leak denylist {} is missing or empty. The scan cannot find "
            "personal names or account IDs.\n".format(scan.denylist_path())
        )
    return _report_hits(scan.leak_scan(Path(args.dir)))


def cmd_lint_portable(args):
    from sdlc_lib import scan

    return _report_hits(scan.lint_portable(Path(args.dir)))


def cmd_export(args):
    from sdlc_lib import export

    print(export.export(Path(args.skill_dir), Path(args.out), require_denylist=not args.no_denylist))
    return 0


def build_parser():
    parser = argparse.ArgumentParser(
        prog="sdlc.py", description="opieworks-sdlc engine."
    )
    sub = parser.add_subparsers(dest="command", metavar="command")
    sub.required = True

    p = sub.add_parser("version", help="Print the engine version.")
    p.set_defaults(func=cmd_version)

    p = sub.add_parser("setup", help="Install a stage in a repo.")
    p.add_argument("stage_dir", help="Path to the stage skill folder.")
    p.add_argument("--repo", help="Target repo. Default: the repo of the current folder.")
    p.add_argument("--open-pr", action="store_true", help="Open the setup PR as the bot.")
    p.set_defaults(func=cmd_setup)

    p = sub.add_parser("push", help="Push a work branch to origin as the bot.")
    p.add_argument("--branch", help="Branch to push. Default: the current branch.")
    p.add_argument("--repo", help="Target repo. Default: the repo of the current folder.")
    p.set_defaults(func=cmd_push)

    p = sub.add_parser("pr", help="Open a PR as the bot.")
    p.add_argument("--title", required=True, help="PR title.")
    group = p.add_mutually_exclusive_group()
    group.add_argument("--body", help="PR body text.")
    group.add_argument("--body-file", help="File with the PR body.")
    p.add_argument("--head", help="Head branch. Default: the current branch.")
    p.add_argument("--base", help="Base branch. Default: the default branch of the repo.")
    p.add_argument("--repo", help="Target repo. Default: the repo of the current folder.")
    p.set_defaults(func=cmd_pr)

    gates_parser = sub.add_parser("gates", help="Manage the GitHub gate rules.")
    gates_sub = gates_parser.add_subparsers(dest="gates_command", metavar="gates-command")
    gates_sub.required = True
    p = gates_sub.add_parser("apply", help="Create the ruleset and the production environment rules.")
    p.add_argument("--repo", help="Target repo. Default: the repo of the current folder.")
    p.set_defaults(func=cmd_gates_apply)

    p = sub.add_parser("check-bot", help="Check every FR-8 gate rule on GitHub.")
    p.add_argument("--repo", help="Target repo. Default: the repo of the current folder.")
    p.set_defaults(func=cmd_check_bot)

    p = sub.add_parser("checks", help="Run the sdlc-checks CI job.")
    p.add_argument("--base", help="Base commit. Default: the pull request in GITHUB_EVENT_PATH.")
    p.add_argument("--head", help="Head commit. The checks read it as data only.")
    p.add_argument("--repo", help="Repo for --base and --head. Default: the repo of the current folder.")
    p.set_defaults(func=cmd_checks)

    p = sub.add_parser("verify", help="Run the verify loop and write evidence.md.")
    p.add_argument("item_id", help="Work item ID, for example 20261010-fix-login.")
    p.add_argument("--upstream", action="append", default=[], help="Upstream link. Repeat for more links.")
    p.add_argument("--fallback", help="What the stage used with no upstream artifact. Default: PR description and diff.")
    p.add_argument("--max-runs", type=int, help="Run limit for this call. Default: testing.max_runs in the config.")
    p.add_argument("--stage-dir", help="Stage skill folder. Default: the skill folder that holds this engine.")
    p.add_argument("--repo", help="Target repo. Default: the repo of the current folder.")
    p.set_defaults(func=cmd_verify)

    p = sub.add_parser("propose-verify", help="Propose a verify command for the repo.")
    p.add_argument("--repo", help="Target repo. Default: the repo of the current folder.")
    p.set_defaults(func=cmd_propose_verify)

    p = sub.add_parser("run-evals", help="Run the eval tasks and write the eval result.")
    p.add_argument("--harness", required=True, help="Name of an [agent_runner.<harness>] table.")
    p.add_argument("--task", action="append", default=[], help="Run only this task. Repeat for more tasks.")
    p.add_argument("--stage-dir", help="Stage skill folder for role names. Default: the skill folder that holds this engine.")
    p.add_argument("--repo", help="Target repo. Default: the repo of the current folder.")
    p.set_defaults(func=cmd_run_evals)

    for name, func, text in (
        ("resolve", cmd_resolve, "Print the file that a relative path resolves to."),
        ("eject", cmd_eject, "Copy a bundle default into the overlay."),
    ):
        p = sub.add_parser(name, help=text)
        p.add_argument("relpath", help="Path relative to the stage folder.")
        p.add_argument("--stage-dir", help="Stage skill folder. Default: the skill folder that holds this engine.")
        p.add_argument("--repo", help="Target repo. Default: the repo of the current folder.")
        p.set_defaults(func=func)

    work = sub.add_parser("work", help="Manage work items.")
    work_sub = work.add_subparsers(dest="work_command", metavar="work-command")
    work_sub.required = True
    p = work_sub.add_parser("new", help="Make a new work item.")
    p.add_argument("title", help="Short title of the work item.")
    p.add_argument("--lane", required=True, choices=("fast", "full"), help="Lane of the work item.")
    p.add_argument("--repo", help="Target repo. Default: the repo of the current folder.")
    p.set_defaults(func=cmd_work_new)
    p = work_sub.add_parser("show", help="List the artifacts of a work item.")
    p.add_argument("item_id", help="Work item ID, for example 20261010-fix-login.")
    p.add_argument("--repo", help="Target repo. Default: the repo of the current folder.")
    p.set_defaults(func=cmd_work_show)

    p = sub.add_parser("role", help="Run a role in a fresh subprocess.")
    p.add_argument("spec", help="Role name, or a path to the role spec file.")
    p.add_argument(
        "--stage-dir",
        help="Stage skill folder for a role name. Default: the skill folder that holds this engine.",
    )
    p.add_argument("--harness", required=True, help="Name of an [agent_runner.<harness>] table.")
    p.add_argument("--input", action="append", default=[], help="Input file. Repeat for more files.")
    p.add_argument("--task", help="Task text for the role. It goes in the prompt after the role spec.")
    p.add_argument("--repo", help="Target repo. Default: the repo of the current folder.")
    p.add_argument("--dry-run", action="store_true", help="Print the argv and the env key names only.")
    p.set_defaults(func=cmd_role)

    p = sub.add_parser("leak-scan", help="Scan a folder for leaks.")
    p.add_argument("dir", help="Folder to scan.")
    p.set_defaults(func=cmd_leak_scan)

    p = sub.add_parser("lint-portable", help="Scan a folder for harness-specific text.")
    p.add_argument("dir", help="Folder to scan.")
    p.set_defaults(func=cmd_lint_portable)

    p = sub.add_parser("export", help="Write a standalone bundle of a skill.")
    p.add_argument("skill_dir", help="Path to the skill folder.")
    p.add_argument("--out", required=True, help="Output folder. It must not exist.")
    p.add_argument(
        "--no-denylist", action="store_true",
        help="Export with no leak denylist. The scan then cannot find personal names.",
    )
    p.set_defaults(func=cmd_export)

    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    from sdlc_lib.common import SdlcError

    try:
        code = args.func(args)
        # Flush here, so that a closed pipe shows up inside this try.
        sys.stdout.flush()
        return code
    except SdlcError as exc:
        sys.stderr.write("error: {}\n".format(exc.message))
        return exc.code
    except KeyboardInterrupt:
        sys.stderr.write("error: The {} command was stopped.\n".format(args.command))
        return 130
    except BrokenPipeError:
        # The reader closed the pipe, for example "| head". Send the rest of
        # stdout to devnull, so that the flush at exit does not fail.
        devnull = os.open(os.devnull, os.O_WRONLY)
        os.dup2(devnull, sys.stdout.fileno())
        return 1
    except UnicodeDecodeError:
        sys.stderr.write("error: The {} command found a file that is not UTF-8 text.\n".format(args.command))
        return 1
    except NotImplementedError:
        sys.stderr.write("error: The {} command is not available yet.\n".format(args.command))
        return 1
    except ImportError as exc:
        sys.stderr.write("error: The {} command cannot load its module: {}.\n".format(args.command, exc.name))
        return 1
    except OSError as exc:
        name = exc.filename if exc.filename else "a file"
        sys.stderr.write("error: The {} command cannot use {}: {}.\n".format(args.command, name, exc.strerror or exc))
        return 1


if __name__ == "__main__":
    sys.exit(main())
