# knowledge_brain

Flow documents for the applications LogiTestHub tests: one Markdown file per business flow (chit scheme, lot inward,
tagging, billing ...), written from the application's code. The **Test Assistant** reads the docs that match what the
user is talking about. It then knows the screens, rules, branches and tables before it writes a test, and asks the
user to pick a branch instead of guessing.

```
knowledge_brain/
  retail/                 <- project "Retail" (folder = project name, lower-case, spaces -> -)
    chit-scheme.md
    lot-inward.md
```

## Format of a flow doc

```markdown
# Chit scheme: join, pay instalments, complete
keywords: chit, scheme, instalment, installment, payment, join, closing, amount to weight

## Screens
Page paths after {{base_url}}, what each screen does.

## Rules
Conditions the app enforces (e.g. next instalment only after N days), with the exact message shown, file:line.

## Form fill order
One numbered list per form, in the order a user fills it. Mark each field that reloads or clears another one
(e.g. "Branch → reloads Scheme"). Tests follow this order. When a form is not listed here, the Test Assistant
asks the user for the order instead of guessing.

## Branches the tester must choose
Payment mode (Cash / Card / UPI ...), scheme type, with / without advance ... with the exact option texts.

## Data (tables)
table.column and what the status values mean, for Remember SQL / Assert SQL / Set up SQL.

## Test notes
Time travel with Set up SQL, test data to use, known traps.
```

- `keywords:` decides when the doc is used: put the words users say (Tamil / Tanglish words too, e.g. `chit, scheme, thavanai`).
- Take everything from the code (file:line), never from memory. Mark guesses as *unconfirmed*.
- No passwords, keys or customer data.
- When a run teaches something new about a flow (a rule, a message, a trap), add it here as well as a lesson.
