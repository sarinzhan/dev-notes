---
title: Git commands and workflows
tags: [git, version-control, rebase, merge, conflicts]
learned: 2026-10-22
---
## TL;DR
Git is a version control system with three local areas: the **working directory** (your files), the **index / staging area** (what goes into the next commit, filled by `git add`) and the **local repository** (the commit history, written by `git commit`); `push`/`fetch` sync with a remote. Everything else is moving commits and pointers around: `merge` keeps history, `rebase` rewrites it linearly, `reset` moves the branch, `revert` adds an undo commit, `reflog` finds what you thought you lost.

## Key points
### Areas and file states
![How git commands move changes between areas](assets/git-command-flow.png)
- **Untracked**: file exists but Git does not track it. **Modified**: tracked and changed in the working directory. **Staged**: change added to the index, ready for the next commit. **Committed**: saved in the local repository.
- `git status` shows all four; `git diff` compares working directory to index, `git diff --staged` index to `HEAD`.
- `HEAD` points at the current commit, usually through a branch (a movable pointer); a tag is a fixed pointer.

### Syncing with the remote
- `git fetch` downloads all updates from the remote into the local repository (`origin/main` moves) but does not merge them into your branch. Safe anytime.
- `git pull` = `git fetch` + `git merge origin/<branch>` into the current branch (two operations in one). `git pull --rebase` replays your local commits on top instead of creating a merge commit; set `pull.rebase=true` for feature branches.
- `git push`, `git push -u origin feature/x` (set upstream), `git push --force-with-lease` (safe force: fails if someone else pushed in the meantime; never plain `--force` on shared branches).

### Branching
- `git checkout <branch>` switches; `git checkout -b <new>` creates and switches. Modern split: `git switch <branch>` / `git switch -c <new>` for branches, `git restore <file>` for files (Git 2.23+). `git checkout <commit> -- <file>` restores a file from a commit.
- `git branch -d` deletes a merged branch, `-D` forces.

### Combining work: merge vs rebase vs squash
- **merge**: joins changes from one branch into another, keeps the full history and adds a merge commit (unless fast-forward). Branching stays visible. Use it to bring a finished feature into `main` or `develop`.
- **rebase**: takes the commits of the current branch and replays them on top of the target branch as if they were made after it. Result: linear history without merge commits. It *rewrites* commits (new hashes), so use it on your own local or personal branches, never on branches others have pulled. Typical: `git fetch && git rebase origin/main` before opening a merge request.
- **squash**: combine several small commits into one so history looks clean; most often via interactive rebase (`git rebase -i HEAD~5`, mark commits `squash`/`fixup`) or `git merge --squash feature` when merging. GitLab/GitHub offer "squash on merge".
- **amend**: `git commit --amend` adds staged changes to the last commit (and lets you edit the message); `--no-edit` keeps the message. Also a rewrite: only before pushing, or push with `--force-with-lease`.
- **cherry-pick**: `git cherry-pick <sha> [<sha>...]` applies one or more commits from another branch onto the current one (hotfix from `main` to a release branch). `-x` records the origin in the message.

### Undoing
- **stash**: saves the working directory and index changes so you can switch branches without committing. `git stash` or `git stash push -m "wip: retry logic"` saves; `git stash list`; `git stash pop` reapplies and removes the entry; `git stash apply` reapplies and keeps it; `git stash -u` includes untracked files; `git stash drop`.
- **reset**: moves the current branch (HEAD) to another commit and optionally adjusts index and working directory. `--soft`: changes stay staged, working directory untouched (`git reset --soft HEAD~3` to re-commit three commits as one). `--mixed` (default): clears the index, working directory untouched (changes become unstaged). `--hard`: clears index and working directory; uncommitted work is gone. `git reset <file>` unstages one file (`git restore --staged <file>` is the modern form).
- **revert**: creates a new commit that undoes the changes of a given commit; history is preserved, so it is the right tool for pushed branches. `git revert <sha>` opens the editor for the message; `--no-edit` keeps the default; `git revert HEAD~2..HEAD` reverts a range; `-m 1` for a merge commit.
- **reflog**: `git reflog` shows the history of every move of `HEAD` (checkouts, resets, rebases, amends), including commits no longer reachable from any branch. After a bad `reset --hard` or rebase: `git reflog`, find the entry, `git reset --hard HEAD@{3}` or `git branch rescue <sha>`. Entries expire after 90 days.

### Conflicts
- A conflict happens when Git cannot merge changes automatically: both sides changed the same lines (or one deleted a file the other edited). Most common during `merge`, `rebase`, `cherry-pick`, `stash apply/pop`.
- Procedure: `git status` lists conflicted files (`both modified`); open them, resolve the `<<<<<<< HEAD` / `=======` / `>>>>>>> branch` markers (or use `git mergetool`, the IDE); after resolving, add them to the index with `git add <file>`; finish with `git commit` (merge, cherry-pick) or `git rebase --continue`.
- To give up: `git merge --abort`, `git rebase --abort`, `git cherry-pick --abort`. `git checkout --ours|--theirs <file>` takes one side wholesale (note: during a rebase "ours" is the branch being rebased *onto*).

### Inspecting
- `git log --oneline --graph --all`, `git log -p <file>`, `git blame -L 40,60 <file>`, `git show <sha>`, `git bisect` to find the commit that broke a test.

## Example
```bash
# Typical feature-branch cycle with a clean, linear history
git switch -c feature/esim-retry origin/main
# ... work ...
git add -p                                   # stage hunks selectively
git commit -m "Add retry with backoff to SM-DP+ client"
git commit --amend --no-edit                 # forgot a file: fold it into the last commit

git fetch origin
git rebase -i origin/main                    # replay on top of main, squash fixups
# conflict in RetryPolicy.java
git status
$EDITOR src/main/java/.../RetryPolicy.java   # resolve markers
git add src/main/java/.../RetryPolicy.java
git rebase --continue

git push --force-with-lease -u origin feature/esim-retry

# Oops: reset --hard on the wrong branch
git reflog | head -5                         # 3f2a1c0 HEAD@{1}: commit: Add retry with backoff ...
git reset --hard HEAD@{1}

# Undo a bad commit that is already on main, without rewriting history
git revert --no-edit 9b1e7d4 && git push
```

## Pitfalls
- Rebasing or amending commits that others already pulled forces everyone to repair their history; rewrite only private branches, and push rewrites with `--force-with-lease`.
- `git reset --hard` and `git checkout -- <file>` destroy uncommitted work with no reflog entry; stash first.
- `git pull` on a dirty tree with a default merge strategy creates surprise merge commits; use `pull --rebase` or fetch and look first.
- `stash pop` with a conflict leaves the stash in the list; after resolving, drop it manually.
- Secrets committed to history stay there unless rewritten (`git filter-repo`); rotate the secret anyway, every clone already has it.
- Related: [[maven]] (versioning and releases), [[docker-socket-dind-dood]] (CI pipelines), [[linux-commands]].
