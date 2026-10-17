# UI tests (Tester CLI / LogiTestHub)

Each `*_test.md` here is one test case in plain English, one step per line. Commit them with the code they test.

    tester run --file tests/<name>_test.md     run one case
    tester test                          run every case in this folder
    tester run "<what to test>"          new case: runs it and saves it here
    tester case pull <id>                copy a LogiTestHub case here

`helpers/` holds shared steps for `@import` (e.g. `@import ../helpers/login.md`). `variables/` holds the test
data (`{"username": {"value": ".."}, "password": {"value": "..", "secret": true}}`): used by every run here, kept out of git.
LogiTestHub (Settings -> System) uses these two folders too when it runs on this PC.

`tester.json` keeps this project's LogiTestHub project and site URL. A `case_id:` line in a case's front matter links
the file to its LogiTestHub case, so its runs are saved under that case.
