"""GitHub gate rules (FR-8, FR-93). Owner: the gates module author.

`apply` makes the branch ruleset and the production environment rules.
`check_bot` reads them back and gives a pass or fail for each FR-8 rule.
Both use the gh login of the person who runs them, because only a repo
admin can change or fully read the rules. The bot never runs them.
"""

from __future__ import annotations

import base64
from dataclasses import dataclass

from . import github
from .common import SdlcError

RULESET_NAME = "opieworks-sdlc"
REQUIRED_CHECK = "sdlc-checks"
CHECKS_WORKFLOW = ".github/workflows/sdlc-checks.yml"
ENVIRONMENT = "production"
OWNER_KEY = "codeowners.owner"
CODEOWNERS_LOOKUP = (".github/CODEOWNERS", "CODEOWNERS", "docs/CODEOWNERS")
# The only bypass actor that check-bot accepts: the repository admin role.
ADMIN_ROLE_ID = 5
SAFE_BOT_ROLES = ("read", "triage", "write")


@dataclass(frozen=True)
class RuleResult:
    rule: str
    ok: bool
    detail: str

    def format(self) -> str:
        return f"{'PASS' if self.ok else 'FAIL'} {self.rule}: {self.detail}"


def ruleset_payload() -> dict:
    """Return the branch ruleset for the default branch (section 9 of the spec)."""
    return {
        "name": RULESET_NAME,
        "target": "branch",
        "enforcement": "active",
        "bypass_actors": [],
        "conditions": {"ref_name": {"include": ["~DEFAULT_BRANCH"], "exclude": []}},
        "rules": [
            {
                "type": "pull_request",
                "parameters": {
                    "required_approving_review_count": 1,
                    "require_code_owner_review": True,
                    "dismiss_stale_reviews_on_push": True,
                    "require_last_push_approval": True,
                    "required_review_thread_resolution": False,
                },
            },
            {
                "type": "required_status_checks",
                "parameters": {
                    "strict_required_status_checks_policy": False,
                    "required_status_checks": [{"context": REQUIRED_CHECK}],
                },
            },
        ],
    }


def _reviewer(config: dict, bot: str) -> str:
    table = config.get("codeowners")
    value = table.get("owner") if isinstance(table, dict) else None
    if not isinstance(value, str) or not value.strip():
        raise SdlcError(f"The config key {OWNER_KEY} is missing. It names the human who owns the gates.")
    login = value.strip().removeprefix("@")
    if "/" in login:
        raise SdlcError(f"The config key {OWNER_KEY} is a team. gates apply supports a user reviewer only.")
    if login.casefold() == bot.casefold():
        raise SdlcError(f"The config key {OWNER_KEY} names the bot. The bot must never own a gate.")
    return login


def apply(slug: str, config: dict) -> list[str]:
    """Create or update the ruleset and the production environment. Return the actions.

    FR-93: refuse until the default branch has the sdlc-checks workflow,
    which comes in the first setup PR.
    """
    bot = github.bot_login(config)
    reviewer = _reviewer(config, bot)
    branch = github.default_branch(slug)
    try:
        github.api("GET", f"repos/{slug}/contents/{CHECKS_WORKFLOW}?ref={branch}")
    except github.NotFound:
        raise SdlcError(
            f"The default branch {branch} has no {CHECKS_WORKFLOW}. Merge the first setup PR, then run gates apply."
        ) from None

    actions = []
    payload = ruleset_payload()
    existing = [
        r for r in github.api("GET", f"repos/{slug}/rulesets?per_page=100") or []
        if r.get("name") == RULESET_NAME and r.get("source_type", "Repository") == "Repository"
    ]
    if existing:
        github.api("PUT", f"repos/{slug}/rulesets/{existing[0]['id']}", payload)
        actions.append("changed ruleset " + RULESET_NAME)
    else:
        github.api("POST", f"repos/{slug}/rulesets", payload)
        actions.append(f"created ruleset {RULESET_NAME}")

    user = github.api("GET", f"users/{reviewer}")
    github.api("PUT", f"repos/{slug}/environments/{ENVIRONMENT}", {
        "reviewers": [{"type": "User", "id": user["id"]}],
        "prevent_self_review": True,
        "deployment_branch_policy": {"protected_branches": False, "custom_branch_policies": True},
    })
    actions.append(f"set environment {ENVIRONMENT}: reviewer {reviewer}, self-review prevented")

    policies = github.api("GET", f"repos/{slug}/environments/{ENVIRONMENT}/deployment-branch-policies?per_page=100")
    found = False
    for policy in (policies or {}).get("branch_policies", []):
        if policy.get("name") == branch and policy.get("type", "branch") == "branch":
            found = True
            continue
        github.api("DELETE", f"repos/{slug}/environments/{ENVIRONMENT}/deployment-branch-policies/{policy['id']}")
        actions.append(f"removed deployment branch policy {policy.get('name')}")
    if not found:
        github.api("POST", f"repos/{slug}/environments/{ENVIRONMENT}/deployment-branch-policies",
                   {"name": branch, "type": "branch"})
        actions.append(f"added deployment branch policy {branch}")
    return actions


def _codeowners(slug: str, branch: str) -> tuple[str, str] | None:
    """Return (path, text) of the CODEOWNERS file that GitHub reads, or None."""
    for rel in CODEOWNERS_LOOKUP:
        try:
            data = github.api("GET", f"repos/{slug}/contents/{rel}?ref={branch}")
        except github.NotFound:
            continue
        return rel, base64.b64decode(data.get("content", "")).decode("utf-8")
    return None


def _owners(text: str) -> set[str]:
    owners = set()
    for line in text.splitlines():
        line = line.split("#", 1)[0].strip()
        for word in line.split()[1:]:
            if word.startswith("@"):
                owners.add(word[1:].casefold())
    return owners


def check_bot(slug: str, config: dict) -> list[RuleResult]:
    """Return one RuleResult per FR-8 rule. See CONTRACT.md."""
    bot = github.bot_login(config)
    branch = github.default_branch(slug)
    results = []

    def add(rule, ok, detail):
        results.append(RuleResult(rule, bool(ok), detail))

    found = _codeowners(slug, branch)
    owners = _owners(found[1]) if found else set()
    add("codeowners-present", owners, f"{found[0]} names {len(owners)} owner(s)" if found else "no CODEOWNERS file")
    add("bot-not-code-owner", bot.casefold() not in owners,
        f"{bot} is a code owner" if bot.casefold() in owners else f"{bot} is not a code owner")

    try:
        perm = github.api("GET", f"repos/{slug}/collaborators/{bot}/permission")
        role_name = perm.get("role_name", perm.get("permission", "unknown"))
        add("bot-no-admin-rights", role_name in SAFE_BOT_ROLES, f"{bot} has the role {role_name}")
    except github.NotFound:
        add("bot-no-admin-rights", False, f"{bot} is not a collaborator")

    rules = github.api("GET", f"repos/{slug}/rules/branches/{branch}?per_page=100") or []
    prs = [r.get("parameters", {}) for r in rules if r.get("type") == "pull_request"]
    count = max((p.get("required_approving_review_count", 0) for p in prs), default=0)
    add("pr-required", prs and count >= 1, f"pull_request rule with {count} required approval(s)" if prs else "no pull_request rule")
    for rule, key in (
        ("code-owner-review", "require_code_owner_review"),
        ("dismiss-stale-approvals", "dismiss_stale_reviews_on_push"),
        ("approve-latest-push", "require_last_push_approval"),
    ):
        ok = any(p.get(key) is True for p in prs)
        add(rule, ok, f"{key} is {'on' if ok else 'off'}")
    contexts = {
        c.get("context")
        for r in rules if r.get("type") == "required_status_checks"
        for c in r.get("parameters", {}).get("required_status_checks", [])
    }
    add("required-check", REQUIRED_CHECK in contexts,
        f"{REQUIRED_CHECK} is {'required' if REQUIRED_CHECK in contexts else 'not required'}")

    bad = []
    for ruleset_id in sorted({r["ruleset_id"] for r in rules if "ruleset_id" in r}):
        ruleset = github.api("GET", f"repos/{slug}/rulesets/{ruleset_id}")
        for actor in ruleset.get("bypass_actors") or []:
            admin = actor.get("actor_type") == "RepositoryRole" and actor.get("actor_id") == ADMIN_ROLE_ID
            if not admin and actor.get("actor_type") != "OrganizationAdmin":
                bad.append(f"{actor.get('actor_type')}:{actor.get('actor_id')}")
    add("no-bot-bypass", not bad, "bypass actors: " + ", ".join(bad) if bad else "only admins can bypass")

    try:
        env = github.api("GET", f"repos/{slug}/environments/{ENVIRONMENT}")
    except github.NotFound:
        for rule in ("env-human-reviewer", "env-prevent-self-review", "env-main-only"):
            add(rule, False, f"no {ENVIRONMENT} environment")
        return results
    review = [p for p in env.get("protection_rules") or [] if p.get("type") == "required_reviewers"]
    logins = [r.get("reviewer", {}).get("login") or r.get("reviewer", {}).get("slug", "")
              for p in review for r in p.get("reviewers") or []]
    human = logins and bot.casefold() not in {x.casefold() for x in logins}
    add("env-human-reviewer", human, "reviewers: " + ", ".join(logins) if logins else "no required reviewer")
    self_review = any(p.get("prevent_self_review") is True for p in review)
    add("env-prevent-self-review", self_review, f"prevent_self_review is {'on' if self_review else 'off'}")
    policy = env.get("deployment_branch_policy") or {}
    names = []
    if policy.get("custom_branch_policies") and not policy.get("protected_branches"):
        data = github.api("GET", f"repos/{slug}/environments/{ENVIRONMENT}/deployment-branch-policies?per_page=100")
        names = [f"{p.get('type', 'branch')}:{p.get('name')}" for p in (data or {}).get("branch_policies", [])]
    main_only = names == [f"branch:{branch}"]
    add("env-main-only", main_only, "deploy refs: " + (", ".join(names) if names else "any branch"))
    return results
