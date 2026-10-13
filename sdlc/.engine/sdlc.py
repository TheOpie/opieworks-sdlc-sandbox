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
    print(github.open_pr(repo, head, args.title, body, config, base=args.base, labels=args.label))
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
    """The test-lock check (FR-41). The build stage owns it."""
    from sdlc_lib import build

    result = build.test_lock(source, base, head)
    return result.lines, result.ok


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


# Stage: build (S3). plan-init and build-check. The test-lock runs in checks.


def cmd_plan_init(args):
    from sdlc_lib import build

    path = build.init_plan(args.item_id, _repo(args), _stage_dir(args), lane=args.lane, links=args.upstream,
                           fallback=args.fallback, request=args.request)
    print("wrote " + path.relative_to(_repo(args)).as_posix())
    return 0


def cmd_build_check(args):
    from sdlc_lib import build, common

    repo = _repo(args)
    result = build.build_check(args.item_id, repo, args.phase, common.load_config(repo), base=args.base)
    for line in result.lines:
        print(line)
    return result.code


# End of stage: build.


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
        return role.execute_checked(spec, argv, env, repo)
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


# Stage: intent (slice S2) --------------------------------------------------------


def cmd_intent_new(args):
    from sdlc_lib import intent

    repo = _repo(args)
    changes = {"api_change": args.api_change, "token_change": args.token_change, "visual_change": args.visual_change}
    item_id, lane, reason = intent.new_intent(
        args.title, args.kind, args.source, repo, _stage_dir(args), changes=changes, links=args.upstream,
    )
    print(item_id)
    print("proposed lane: {} ({})".format(lane, reason))
    print("wrote " + "sdlc/work/{}/{}".format(item_id, intent.INTENT_NAME))
    return 0


def cmd_intent_issue(args):
    from sdlc_lib import github, intent

    _url, text = intent.issue_text(github.origin_slug(_repo(args)), args.number)
    sys.stdout.write(text)
    return 0


def cmd_intent_check(args):
    from sdlc_lib import common, intent

    repo = _repo(args)
    problems = intent.check_intent(args.item_id, repo, common.load_config(repo))
    for problem in problems:
        print("problem: " + problem)
    print("intent check: " + ("FAIL" if problems else "PASS"))
    return 1 if problems else 0


def cmd_intent_lane(args):
    from sdlc_lib import intent

    changed = intent.set_lane(args.item_id, _repo(args), args.lane)
    print("lane: {}{}".format(args.lane, "" if changed else " (no changes)"))
    return 0


def cmd_intent_pr(args):
    from sdlc_lib import common, intent

    repo = _repo(args)
    print(intent.open_intent_pr(repo, args.item_id, common.load_config(repo)))
    return 0


def cmd_intent_status(args):
    from sdlc_lib import github, intent

    repo = _repo(args)
    pulls = intent.list_intent_pulls(github.origin_slug(repo))
    states = intent.intent_states(pulls, intent.read_triage(repo, args.ref), intent.read_lanes(repo, args.ref))
    if args.item_id:
        intent.check_item_id(args.item_id)
        states = [s for s in states if s.item_id == args.item_id]
        if not states:
            print("{}  no intent PR".format(args.item_id))
            return 0
    if not states:
        print("No intent PR found with the label {}.".format(intent.LABEL))
    for state in states:
        print(state.format())
    return 0


def cmd_intent_record_reason(args):
    from sdlc_lib import github, intent

    repo = _repo(args)
    intent.check_item_id(args.item_id)
    pull = intent.find_pull(intent.list_intent_pulls(github.origin_slug(repo)), args.item_id)
    path = intent.record_reason(repo, args.item_id, args.reason, pull)
    print("wrote " + path.relative_to(repo).as_posix())
    return 0


def add_intent_parsers(sub):
    """The intent stage commands (slice S2)."""
    intent_parser = sub.add_parser("intent", help="Intent stage: intent.md, the lane, the intent PR, and its gate state.")
    intent_sub = intent_parser.add_subparsers(dest="intent_command", metavar="intent-command")
    intent_sub.required = True

    p = intent_sub.add_parser("new", help="Make a work item and write intent.md from the template.")
    p.add_argument("title", help="Short title of the work item.")
    p.add_argument("--kind", required=True, choices=("bug", "feature"), help="Kind of the work item.")
    p.add_argument("--source", required=True, choices=("text", "thread", "issue"), help="Kind of input.")
    p.add_argument("--upstream", action="append", default=[], help="Upstream link, for example the issue URL. Repeat for more links.")
    for name in ("api", "token", "visual"):
        p.add_argument("--{}-change".format(name), default="unknown", choices=("yes", "no", "unknown"),
                       help="Does the work change the {}? Default: unknown.".format(name))
    p.add_argument("--stage-dir", help="Stage skill folder. Default: the skill folder that holds this engine.")
    p.add_argument("--repo", help="Target repo. Default: the repo of the current folder.")
    p.set_defaults(func=cmd_intent_new)

    p = intent_sub.add_parser("issue", help="Print a GitHub issue and its comments as text. Read only.")
    p.add_argument("number", type=int, help="Issue number.")
    p.add_argument("--repo", help="Target repo. Default: the repo of the current folder.")
    p.set_defaults(func=cmd_intent_issue)

    p = intent_sub.add_parser("check", help="Check that intent.md is complete.")
    p.add_argument("item_id", help="Work item ID.")
    p.add_argument("--repo", help="Target repo. Default: the repo of the current folder.")
    p.set_defaults(func=cmd_intent_check)

    p = intent_sub.add_parser("lane", help="Set the lane in intent.md and item.toml.")
    p.add_argument("item_id", help="Work item ID.")
    p.add_argument("lane", choices=("fast", "full"), help="The lane.")
    p.add_argument("--repo", help="Target repo. Default: the repo of the current folder.")
    p.set_defaults(func=cmd_intent_lane)

    p = intent_sub.add_parser("pr", help="Open the intent PR as the bot with the label sdlc:intent.")
    p.add_argument("item_id", help="Work item ID.")
    p.add_argument("--repo", help="Target repo. Default: the repo of the current folder.")
    p.set_defaults(func=cmd_intent_pr)

    p = intent_sub.add_parser("status", help="Show the gate state of intent PRs. Read only.")
    p.add_argument("item_id", nargs="?", help="Show only this work item.")
    p.add_argument("--ref", default="HEAD", help="Git ref to read triage records and lanes from. Default: HEAD.")
    p.add_argument("--repo", help="Target repo. Default: the repo of the current folder.")
    p.set_defaults(func=cmd_intent_status)

    p = intent_sub.add_parser("record-reason", help="Write the reason of a human who closed an intent PR to sdlc/triage/.")
    p.add_argument("item_id", help="Work item ID.")
    p.add_argument("--reason", required=True, help="The reason, in the words of the human.")
    p.add_argument("--repo", help="Target repo. Default: the repo of the current folder.")
    p.set_defaults(func=cmd_intent_record_reason)

# End of stage: intent ------------------------------------------------------------


# Stage: design (S4) ------------------------------------------------------------


def cmd_policies(args):
    from sdlc_lib import common, design

    repo = _repo(args)
    found = design.policy_paths(repo, common.load_config(repo))
    if not found:
        print("No policy files are configured. Add [design].policies to sdlc/config.toml.")
    for rel in found:
        print(rel)
    return 0


def _print_flags(flags):
    open_flags = [flag for flag in flags if flag.open]
    print("flags: {} ({} open)".format(len(flags), len(open_flags)))
    for flag in open_flags:
        print("open flag: {} {}".format(flag.id, flag.text))
    return open_flags


def cmd_spec(args):
    from sdlc_lib import common, design

    repo = _repo(args)
    stage_dir = _stage_dir(args)
    draft = Path(args.draft).read_text(encoding="utf-8")
    problem = args.problem
    if args.problem_file:
        problem = Path(args.problem_file).read_text(encoding="utf-8")
    path, flags = design.write_spec(
        args.item_id, repo, stage_dir, draft, common.load_config(repo), problem=problem, links=args.upstream or None,
    )
    print("wrote " + path.relative_to(repo).as_posix())
    if _print_flags(flags):
        print("The gate owner resolves each open flag before the spec merges.")
    return 0


def cmd_spec_check(args):
    from sdlc_lib import common, design

    repo = _repo(args)
    flags = design.check_spec(args.item_id, repo, common.load_config(repo))
    if _print_flags(flags):
        print("spec-check: FAIL. Resolve each open flag before the spec merges.")
        return 1
    print("spec-check: PASS")
    return 0

# End of stage: design ----------------------------------------------------------


# Stage: deploy (S5). The deploy commands are in this one block. -------------


def cmd_deploy_review_post(args):
    from sdlc_lib import common, deploy, github

    repo = _repo(args)
    config = common.load_config(repo)
    nit_cap = deploy.int_setting(config, "nit_cap", 0)
    review = deploy.parse_findings(Path(args.findings).read_text(encoding="utf-8"))
    path = deploy.write_review(args.item_id, repo, _stage_dir(args), review, nit_cap,
                               links=args.upstream, fallback=args.fallback)
    print("verdict: " + review.verdict)
    print("findings: " + deploy.counts(review, nit_cap))
    print("wrote " + path.relative_to(repo).as_posix())
    if args.pr is not None:
        body = deploy.comment_body(path.read_text(encoding="utf-8"))
        print(deploy.post_comment(github.origin_slug(repo), args.pr, body, config, dry_run=args.dry_run))
    return 0


def cmd_deploy_babysit(args):
    from sdlc_lib import common, deploy

    repo = _repo(args)
    result = deploy.babysit(repo, _stage_dir(args), args.pr, args.harness, common.load_config(repo),
                            dry_run=args.dry_run)
    for line in result.lines:
        print(line)
    return 0


def cmd_deploy_canary(args):
    from sdlc_lib import common, deploy

    repo = _repo(args)
    print(deploy.canary(repo, args.item_id, args.tag, args.target, common.load_config(repo), dry_run=args.dry_run))
    return 0


def _print_production(run):
    for line in run.lines:
        print(line)
    return 0


def cmd_deploy_production(args):
    from sdlc_lib import common, deploy

    repo = _repo(args)
    return _print_production(deploy.production(repo, args.source, args.item, common.load_config(repo), dry_run=args.dry_run))


def cmd_deploy_history(args):
    from sdlc_lib import common, deploy

    repo = _repo(args)
    for row in deploy.history(repo, common.load_config(repo), limit=args.limit):
        print("{}  {}  {}  source {}  {}".format(
            row["created"], row["status"], row["conclusion"] or "-", (row["source"] or "unknown")[:12], row["url"]))
    return 0


def cmd_deploy_rollback(args):
    from sdlc_lib import common, deploy

    repo = _repo(args)
    config = common.load_config(repo)
    target = args.to
    if target is None:
        current, target = deploy.rollback_source(deploy.history(repo, config, limit=30, dry_run=args.dry_run))
        print("current production source {}, roll back to {}".format(current[:12], target[:12]))
    return _print_production(deploy.production(repo, target, args.item, config, dry_run=args.dry_run))


def cmd_deploy_build_docs(args):
    from sdlc_lib import common, deploy

    repo = _repo(args)
    info = deploy.build_docs(repo, common.load_config(repo), args.source, Path(args.out))
    print("built the docs of {} into {}".format(info["source"], args.out))
    return 0


def _add_deploy_parsers(sub):
    """Stage: deploy (S5). Add the deploy command group."""
    deploy_parser = sub.add_parser("deploy", help="Deploy stage: review, babysit, canary, production, rollback.")
    dsub = deploy_parser.add_subparsers(dest="deploy_command", metavar="deploy-command")
    dsub.required = True
    repo_help = "Target repo. Default: the repo of the current folder."
    stage_help = "Stage skill folder. Default: the skill folder that holds this engine."
    dry_help = "Print the GitHub call. Do not call GitHub as the bot and do not read the bot token file."

    p = dsub.add_parser("review-post", help="Rank the reviewer findings, write review.md, and post it as the bot.")
    p.add_argument("item_id", help="Work item ID, for example 20261010-fix-login.")
    p.add_argument("--findings", required=True, help="File with the reviewer output.")
    p.add_argument("--pr", type=int, help="PR number. With no PR, the command writes review.md only.")
    p.add_argument("--upstream", action="append", default=[], help="Upstream link. Repeat for more links.")
    p.add_argument("--fallback", help="What the stage used with no upstream artifact. Default: PR description.")
    p.add_argument("--dry-run", action="store_true", help=dry_help)
    p.add_argument("--stage-dir", help=stage_help)
    p.add_argument("--repo", help=repo_help)
    p.set_defaults(func=cmd_deploy_review_post)

    p = dsub.add_parser("babysit", help="Run one babysit round on a PR, up to the round limit.")
    p.add_argument("pr", type=int, help="PR number.")
    p.add_argument("--harness", required=True, help="Name of an [agent_runner.<harness>] table.")
    p.add_argument("--dry-run", action="store_true", help="Read the PR with your login. Do not run the role, push, or post.")
    p.add_argument("--stage-dir", help=stage_help)
    p.add_argument("--repo", help=repo_help)
    p.set_defaults(func=cmd_deploy_babysit)

    p = dsub.add_parser("canary", help="Create a canary prerelease as the bot. Never a full release.")
    p.add_argument("item_id", help="Work item ID.")
    p.add_argument("--tag", required=True, help="New tag for the prerelease, for example v0.2.0-canary.1.")
    p.add_argument("--target", help="Branch or commit for a new tag. Default: the default branch.")
    p.add_argument("--dry-run", action="store_true", help=dry_help)
    p.add_argument("--repo", help=repo_help)
    p.set_defaults(func=cmd_deploy_canary)

    p = dsub.add_parser("production", help="Start the production workflow on the default branch as the bot.")
    p.add_argument("--source", help="Commit to deploy. It must be in the history of the default branch. Default: its head.")
    p.add_argument("--item", help="Work item ID for the run name.")
    p.add_argument("--dry-run", action="store_true", help=dry_help)
    p.add_argument("--repo", help=repo_help)
    p.set_defaults(func=cmd_deploy_production)

    p = dsub.add_parser("history", help="List the recent production runs and their source commits.")
    p.add_argument("--limit", type=int, default=10, help="Number of runs. Default: 10.")
    p.add_argument("--repo", help=repo_help)
    p.set_defaults(func=cmd_deploy_history)

    p = dsub.add_parser("rollback", help="Start the production workflow with the prior source commit.")
    p.add_argument("--to", help="Commit to roll back to. Default: the source of the prior successful run.")
    p.add_argument("--item", help="Work item ID for the run name.")
    p.add_argument("--dry-run", action="store_true", help=dry_help)
    p.add_argument("--repo", help=repo_help)
    p.set_defaults(func=cmd_deploy_rollback)

    p = dsub.add_parser("build-docs", help="CI: build the docs of a source commit for the production job.")
    p.add_argument("--source", default="", help="Commit to build. Default: HEAD.")
    p.add_argument("--out", required=True, help="Empty or absent output folder.")
    p.add_argument("--repo", help=repo_help)
    p.set_defaults(func=cmd_deploy_build_docs)


# Stage: maintain (S6). The maintain stage commands. -------------------------


def cmd_detect(args):
    from sdlc_lib import common, maintain

    repo = _repo(args)
    config = common.load_config(repo)
    window = maintain.window_size(config)
    metrics = maintain.select_metrics(repo, args.metric or None, args.command)
    report = maintain.detect(repo, metrics, window, record=not args.dry_run)
    for r in report.results:
        if r.error:
            print("{}: error: {}".format(r.metric, r.error))
            continue
        print("{}: value {} mean {} sigma {} z {} tier {}".format(
            r.metric, maintain.fmt(r.value), maintain.fmt(r.mean) or "none",
            maintain.fmt(r.sigma) or "none", maintain.fmt(r.z) or "none", r.tier))
    if report.commit:
        print("recorded the readings on the {} branch at {}".format(maintain.DATA_BRANCH, report.commit[:12]))
    else:
        print("dry run: recorded nothing")
    if args.issues:
        for line in _detect_issues(repo, config, report, maintain):
            print(line)
    return 1 if report.failed else 0


def _detect_issues(repo, config, report, maintain):
    """Write the issues. In GitHub Actions, use the job token. Else act as the bot."""
    from sdlc_lib import github

    if os.environ.get("GITHUB_ACTIONS") == "true" and os.environ.get("GITHUB_REPOSITORY"):
        return maintain.file_issues(os.environ["GITHUB_REPOSITORY"], report)
    slug = github.origin_slug(repo)
    env = github.bot_env(config)
    try:
        return maintain.file_issues(slug, report, env=env)
    finally:
        github.cleanup(env)


def cmd_readings(args):
    from sdlc_lib import maintain

    sys.stdout.write(maintain.readings_text(_repo(args), maintain.check_metric_name(args.metric)))
    return 0


def cmd_last_run(args):
    from sdlc_lib import maintain

    data = maintain.last_run(_repo(args))
    if data is None:
        print("no detect run recorded")
        return 1
    print(data["time"])
    return 0


def cmd_finding(args):
    from sdlc_lib import maintain

    repo = _repo(args)
    item_id, path = maintain.new_finding(repo, _stage_dir(args), maintain.check_metric_name(args.metric),
                                         issue=args.issue, lane=args.lane)
    print(item_id)
    print("wrote " + path.relative_to(repo).as_posix())
    return 0


def cmd_triage(args):
    from sdlc_lib import maintain

    repo = _repo(args)
    path = maintain.triage(repo, args.item_id, args.decision, reason=args.reason, issue=args.issue)
    print("wrote " + path.relative_to(repo).as_posix())
    return 0


def cmd_lesson(args):
    from sdlc_lib import maintain

    repo = _repo(args)
    path = maintain.new_lesson(repo, _stage_dir(args), args.item_id, pr=args.pr)
    print("wrote " + path.relative_to(repo).as_posix())
    return 0


def cmd_close_check(args):
    from sdlc_lib import maintain

    problems = maintain.close_check(_repo(args), args.item_id)
    for line in problems:
        print("FAIL " + line)
    print("close-check: " + ("FAIL" if problems else "PASS"))
    return 1 if problems else 0


def _add_maintain_commands(sub):
    repo_help = "Target repo. Default: the repo of the current folder."
    stage_help = "Stage skill folder. Default: the skill folder that holds this engine."

    p = sub.add_parser("detect", help="Measure the metrics and give each reading a tier. No model.")
    p.add_argument("--metric", action="append", default=[], help="Measure only this metric. Repeat for more.")
    p.add_argument("--command", help="Fallback: a command that prints a number. Needs one --metric.")
    p.add_argument("--issues", action="store_true", help="Open or comment on one issue per metric and tier at 2 and 3 sigma.")
    p.add_argument("--dry-run", action="store_true", help="Print the tiers. Record nothing.")
    p.add_argument("--repo", help=repo_help)
    p.set_defaults(func=cmd_detect)

    p = sub.add_parser("readings", help="Print the readings of a metric from the sdlc-data branch.")
    p.add_argument("metric", help="Metric name.")
    p.add_argument("--repo", help=repo_help)
    p.set_defaults(func=cmd_readings)

    p = sub.add_parser("last-run", help="Print the time of the last recorded detect run.")
    p.add_argument("--repo", help=repo_help)
    p.set_defaults(func=cmd_last_run)

    p = sub.add_parser("finding", help="Make a work item and an intent.md draft from the last reading of a metric.")
    p.add_argument("metric", help="Metric name.")
    p.add_argument("--issue", type=int, help="Number of the detect issue.")
    p.add_argument("--lane", choices=("fast", "full"), default="full", help="Proposed lane. The human sets it at the Plan gate.")
    p.add_argument("--stage-dir", help=stage_help)
    p.add_argument("--repo", help=repo_help)
    p.set_defaults(func=cmd_finding)

    p = sub.add_parser("triage", help="Add a triage decision for a finding.")
    p.add_argument("item_id", help="Work item ID of the finding.")
    p.add_argument("--decision", required=True, choices=("fix-now", "schedule", "dismiss"), help="The decision of the gate owner.")
    p.add_argument("--reason", help="The reason. A dismiss needs one.")
    p.add_argument("--issue", type=int, help="Number of the detect issue.")
    p.add_argument("--repo", help=repo_help)
    p.set_defaults(func=cmd_triage)

    p = sub.add_parser("lesson", help="Write a lessons entry draft for a merged bug fix.")
    p.add_argument("item_id", help="Work item ID of the bug fix.")
    p.add_argument("--pr", type=int, help="Number of the merged fix PR.")
    p.add_argument("--stage-dir", help=stage_help)
    p.add_argument("--repo", help=repo_help)
    p.set_defaults(func=cmd_lesson)

    p = sub.add_parser("close-check", help="Check that a bug fix has its eval task and its lesson.")
    p.add_argument("item_id", help="Work item ID of the bug fix.")
    p.add_argument("--repo", help=repo_help)
    p.set_defaults(func=cmd_close_check)


# End of stage: maintain. ------------------------------------------------------


# Stage: hub (S7). status and next. Read only. ---------------------------------


def _hub_snapshot(args):
    from sdlc_lib import hub

    return hub.snapshot(_repo(args), ref=args.ref)


def cmd_status(args):
    import json

    from sdlc_lib import hub

    data = hub.status(_hub_snapshot(args))
    if args.json:
        print(json.dumps(data, indent=2, sort_keys=True))
    else:
        for line in hub.render_status(data):
            print(line)
    return 0


def cmd_next(args):
    import json

    from sdlc_lib import hub
    from sdlc_lib.common import SdlcError

    try:
        here = _stage_dir(args)
    except SdlcError:
        here = None
    stages = hub.find_stages(here, [Path(b) for b in args.bundle or []])
    data = hub.next_step(hub.find_item(args.item_id, _hub_snapshot(args)), stages)
    if args.json:
        print(json.dumps(data, indent=2, sort_keys=True))
    else:
        for line in hub.render_next(data):
            print(line)
    return 0


def _add_hub_commands(sub):
    """The hub commands (slice S7)."""
    ref_help = "Git ref with the default branch artifacts. Default: origin/<default branch>."
    p = sub.add_parser("status", help="Show the stage, lane, and gate state of each work item. Read only.")
    p.add_argument("--ref", help=ref_help)
    p.add_argument("--json", action="store_true", help="Print JSON.")
    p.add_argument("--repo", help="Target repo. Default: the repo of the current folder.")
    p.set_defaults(func=cmd_status)

    p = sub.add_parser("next", help="Show the next stage of a work item and the exact command. Read only.")
    p.add_argument("item_id", help="Work item ID.")
    p.add_argument("--ref", help=ref_help)
    p.add_argument("--json", action="store_true", help="Print JSON.")
    p.add_argument("--bundle", action="append", help="A stage bundle folder. Repeat for each bundle.")
    p.add_argument("--stage-dir", help="The skill folder next to the stage folders. Default: the skill of this engine.")
    p.add_argument("--repo", help="Target repo. Default: the repo of the current folder.")
    p.set_defaults(func=cmd_next)


# End of stage: hub. ------------------------------------------------------------


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
    p.add_argument("--label", action="append", default=[],
                   help="Label to add as the bot, for example sdlc:design. The bot makes a missing label. Repeat for more.")
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

    # Stage: build (S3).
    p = sub.add_parser("plan-init", help="Write the plan.md skeleton of a work item for the planner.")
    p.add_argument("item_id", help="Work item ID, for example 20261010-fix-login.")
    p.add_argument("--lane", choices=("fast", "full"), help="Lane. Default: the lane in item.toml or intent.md.")
    p.add_argument("--request", help="The user request. Needed when the work item has no intent.md or spec.md.")
    p.add_argument("--upstream", action="append", default=[], help="Upstream link. Repeat for more links.")
    p.add_argument("--fallback", help="What the stage used with no upstream artifact. Default: user request.")
    p.add_argument("--stage-dir", help="Stage skill folder. Default: the skill folder that holds this engine.")
    p.add_argument("--repo", help="Target repo. Default: the repo of the current folder.")
    p.set_defaults(func=cmd_plan_init)

    p = sub.add_parser("build-check", help="Check a work item before the next build step.")
    p.add_argument("item_id", help="Work item ID, for example 20261010-fix-login.")
    p.add_argument("--phase", required=True, choices=("plan", "test", "code"), help="The build step to check.")
    p.add_argument("--base", help="Base branch for the code phase. Default: origin/HEAD, else main.")
    p.add_argument("--repo", help="Target repo. Default: the repo of the current folder.")
    p.set_defaults(func=cmd_build_check)
    # End of stage: build.

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

    # Stage: intent (slice S2)
    add_intent_parsers(sub)

    # Stage: design (S4)
    p = sub.add_parser("policies", help="Print the configured design policy files.")
    p.add_argument("--repo", help="Target repo. Default: the repo of the current folder.")
    p.set_defaults(func=cmd_policies)

    p = sub.add_parser("spec", help="Check a spec draft and write spec.md.")
    p.add_argument("item_id", help="Work item ID, for example 20261010-add-search.")
    p.add_argument("--draft", required=True, help="File with the spec draft of the spec-writer role.")
    group = p.add_mutually_exclusive_group()
    group.add_argument("--problem", help="Inline problem statement, when the work item has no intent.md.")
    group.add_argument("--problem-file", help="File with the inline problem statement.")
    p.add_argument("--upstream", action="append", default=[], help="Upstream link. Repeat for more links.")
    p.add_argument("--stage-dir", help="Stage skill folder. Default: the skill folder that holds this engine.")
    p.add_argument("--repo", help="Target repo. Default: the repo of the current folder.")
    p.set_defaults(func=cmd_spec)

    p = sub.add_parser("spec-check", help="Check spec.md. Exit 1 while a flag is open.")
    p.add_argument("item_id", help="Work item ID, for example 20261010-add-search.")
    p.add_argument("--repo", help="Target repo. Default: the repo of the current folder.")
    p.set_defaults(func=cmd_spec_check)
    # End of stage: design

    _add_deploy_parsers(sub)  # Stage: deploy (S5)
    _add_maintain_commands(sub)  # Stage: maintain (S6).
    _add_hub_commands(sub)  # Stage: hub (S7).

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
