# data/

State kept between daily runs (written by the search; no need to edit):

- `seen.json` – every reported posting and the day it was first seen, so new ones can be marked 🆕.
- `boards.json` – job boards found automatically for companies listed in `config.yaml` without a `board:`.
  Delete a company's entry to make the next run look again.
