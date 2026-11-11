# flotilla - instructions for Claude Code working on this repository

This is the source of the flotilla plugin. How to test, what every change keeps and how a release is made:
[docs/development.md](../docs/development.md) - read it before your first change. This file lives under `.claude/`
because the repository's root is the plugin's root, where `claude plugin validate --strict` refuses a `CLAUDE.md`.

- **English only**, in code, docs and commit messages. **No private names**: the repository is public, so no names
  of the projects a field test ran on, no people's names, no home directories in anything git tracks.
- **Run the full suite through the lane** (`bin/flotilla lane run --for <branch> -- ...`), in a tree nobody is
  editing, and read the verdict from its last line.
- **Never touch the real Claude Code settings or install records of this machine** (`~/.claude/`, a project's
  `.claude/settings*.json`) to try something: use a scratch project. When a change there is the person's to make,
  give them the exact command to run with `!`.
- **The flotilla guards refuse a Bash heredoc, a variable or a substitution next to a flotilla command**: write the
  script or the commit message to a file and pass the file.
- A deferred finding goes to `TODO.md`, under the section of where it came from, with its `*(checked ...)*` line.
- If `CLAUDE.local.md` exists at the repository's root, it holds the maintainer's own notes and agreements; read it too.
