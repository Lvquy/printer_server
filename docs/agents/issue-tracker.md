# Issue tracker: GitHub

Issues and specs live in Lvquy/printer_server.
Use gh with --repo Lvquy/printer_server.

## Operations

- Create: gh issue create --title "..." --body-file FILE
  Use a temporary Markdown file or heredoc for multiline bodies.
- Read: gh issue view NUMBER --json title,body,labels,comments
- List: gh issue list --state open --json number,title,labels
- Comment: gh issue comment NUMBER --body-file FILE
- Label: gh issue edit NUMBER --add-label LABEL
  or --remove-label LABEL
- Close: gh issue close NUMBER --comment "..."

“Publish to the issue tracker” means create a GitHub issue.
“Fetch the relevant ticket” means read its body, labels and comments.

## Pull requests as a triage surface

PRs as a request surface: no.

## Public repository

Keep credentials, backups, private logs and customer data out of issues.
