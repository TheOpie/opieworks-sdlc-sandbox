# Runbook: roll back production

The opieworks-sdlc deploy stage installs this file one time. After that, the file belongs to you. Change it to fit your repo.

A rollback deploys an earlier build to production. It uses the same production workflow as a normal deploy, so the gate owner approves it in the `production` environment. A rollback needs no code change and no pull request.

`<skill>` is the folder of the opieworks-sdlc-deploy skill. Run the commands in the root of the repo.

## When to roll back

- Production shows a bug, a broken page, or a security problem after a deploy.
- A fix needs more time than the users can wait.

Do not wait for the cause. Roll back first, then find the cause in a new work item.

## Steps

1. Find the production runs and their source commits:

   ```text
   python3 <skill>/core/sdlc.py deploy history
   ```

   The newest successful run is the build that is live now. The build to restore is the newest earlier successful run with a different source commit.

2. Start the rollback. With no `--to`, the engine selects the source of the prior successful run:

   ```text
   python3 <skill>/core/sdlc.py deploy rollback --item <id>
   ```

   To restore a specific build, add `--to <sha>`. The commit must be in the history of the default branch. The workflow refuses any other commit.

3. The gate owner opens the run, reads the source commit in the run name, and approves the `production` environment. The bot cannot approve it.

4. When the run is done, check production:
   - Open the site. Check that the problem is gone.
   - Open `<site>/sdlc-build.json`. The `source` value must be the commit of step 2.

5. Record the rollback. Write a comment on the pull request or the issue of the work item: the time, the bad source commit, the restored source commit, the run URL, and the result of step 4.

6. Open a new work item for the cause. The fix goes through the normal stages. The next normal deploy replaces the rolled-back build.

## If the rollback fails

- The run refuses the source: the commit is not in the history of the default branch. Select a commit from `deploy history`.
- The run waits: the gate owner did not approve the environment yet.
- The build fails: the docs build command of the old commit does not work any more. Select an earlier build, or fix forward through a pull request.
