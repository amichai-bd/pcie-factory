# Issue worktrees

Each issue is implemented in its own worktree here, created from current
`origin/main`:

```sh
git fetch origin main
git worktree add -b <issue>-<slug> worktrees/<issue>-<slug> origin/main
```

Everything in this directory except this file is ignored by git. Remove a
worktree and its branch after its PR merges: `git worktree remove`, then
`git branch -D` (a squash-merged branch is not an ancestor of `main`).
