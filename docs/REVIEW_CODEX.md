# Independent Code Review

## Scope and method

Static review only. The application was not run. Findings distinguish verified defects from suspicions.

## Findings

### 1. A job worker can escape its error handler and strand the whole job

- **Location:** `engine/app/jobs.py:320-325`
- **Severity:** high
- **Confidence:** **Verified by reading the surrounding code.** `_resolve_inputs()` and `registry.require_route()` execute before the `try` at line 334; `_run()` awaits all workers with `asyncio.gather()` at line 433 and has no outer error handler.
- **What breaks:** An exception from either operation escapes `_run_item`, makes `gather` raise, and terminates the job task before lines 435-465 finalize or persist the job. The item has already been changed to `running`, so both it and the parent job can remain `running` forever.
- **Concrete trigger:** Create a job successfully, then delete one input (through the file-delete endpoint or from disk) before that item reaches line 320. `_resolve_inputs()` raises `ConversionError("file_missing")` outside the handler. A route registry change between creation and execution can similarly make `require_route()` raise.

### 2. Concurrent installs of one pack are admitted and can delete/validate each other's payload

- **Location:** `engine/app/installer.py:560-563, 589-596`; destructive interaction at `480-491` and `508-510`
- **Severity:** high
- **Confidence:** **Verified by reading the surrounding code.** The running-state check and insertion of the new `Install` are in two separate critical sections, with validation and filesystem work between them.
- **What breaks:** Two requests can both observe no running install and start two threads for the same `pack.id`. Both share one status entry and cancellation flag and both manipulate the same `pack.root` and `<root>.replacing`. One thread can rename or remove the other's installed tree; a verification failure in one thread can delete the other thread's valid root. Status may describe whichever thread wrote last rather than the payload on disk.
- **Concrete trigger:** Send two simultaneous `POST /api/packs/{same-id}/install` requests while the pack is absent. Both pass line 562 before either writes `_installs[pack.id]` at line 591, then race through the final renames.

### 3. Download cancellation can be missed and the download reported completed

- **Location:** `engine/app/downloader.py:391-393, 452-489, 551-557`
- **Severity:** medium
- **Confidence:** **Verified by reading the surrounding code.** Cancellation is only observed by the progress hook or the exception path; there is no cancellation check immediately before the completed update at line 488.
- **What breaks:** A cancel accepted while yt-dlp is post-processing, or after its final progress hook, need not invoke another hook. The worker then records `completed` despite the user having cancelled. The cancel endpoint initially returns the still-`running` state, so clients can see cancellation requested followed by success.
- **Concrete trigger:** Start an audio extraction or video merge, wait until the phase becomes `Merging streams`, then cancel after the last download hook but before `extract_info()` returns. If post-processing succeeds without another progress hook, line 488 wins.

### 4. The downloader's cancel check/set race leaks cancellation IDs

- **Location:** `engine/app/downloader.py:488-507, 551-557`
- **Severity:** medium
- **Confidence:** **Verified by reading the surrounding code.** `cancel()` releases `_lock` after checking `status`, then mutates `_cancelled` without the lock; the worker also discards from that set without the lock.
- **What breaks:** A completed download ID can remain forever in `_cancelled`, causing unbounded memory growth. This interleaving also demonstrates that the shared cancellation state is not made logically atomic by `_lock`.
- **Concrete trigger:** `cancel()` reads `item.status == "running"` at line 554 and releases the lock; the worker sets `completed` at line 488 and executes the `finally` discard at line 507; `cancel()` then adds the ID at line 556. No later code removes it.

### 5. A failed final rename leaves the previous pack displaced as `.replacing`

- **Location:** `engine/app/installer.py:480-491, 550-553`
- **Severity:** high
- **Confidence:** **Verified by reading the surrounding code.** There is no rollback after `root.rename(retired)` and cleanup only removes the staging directory.
- **What breaks:** If `staging.rename(root)` fails after the old root was renamed, the formerly installed pack is no longer at its expected path. The operation reports failure but leaves partial state (`<root>.replacing`) and does not restore the working version.
- **Concrete trigger:** Have `pack.root` exist, then make the second rename fail—for example, another process creates `root` in the interval after line 484, or the filesystem reports an I/O/permission error. The exception reaches the generic handler; `.replacing` remains.

### 6. Job concurrency is bounded per job, not globally

- **Location:** `engine/app/jobs.py:307-308, 411-433`
- **Severity:** medium
- **Confidence:** **Verified by reading the surrounding code.** Every job constructs its own semaphore; there is no module-level scheduler or shared semaphore.
- **What breaks:** Starting N jobs permits up to `N * config.worker_concurrency()` converters simultaneously. CPU-heavy converters and subprocesses can exhaust CPU, memory, file descriptors, or process limits even though each individual job appears bounded.
- **Concrete trigger:** Submit many conversion jobs concurrently, each with at least `worker_concurrency()` input items. Each `_run` creates an independent semaphore and admits that many workers.

### 7. Completed downloads and jobs accumulate indefinitely in process memory

- **Location:** `engine/app/downloader.py:173, 543-547`; `engine/app/jobs.py:179, 303, 499-500`
- **Severity:** medium
- **Confidence:** **Verified by reading the surrounding code and repository references.** Downloads have no removal/pruning operation. Jobs have `forget()`, but no call site exists under `engine/app`; `recent(limit)` only limits returned results, not retained state.
- **What breaks:** Long-running desktop sessions grow `_downloads` and `_JOBS` without bound. Jobs retain routes, items, warnings, options, output metadata, listener queues, and task objects; large batches amplify the leak.
- **Concrete trigger:** Repeatedly start completed downloads and conversion jobs without restarting the application. Each adds a permanent dictionary entry regardless of `recent(limit=20)`.

### 8. Creating a large batch allocates one coroutine/task per item at once

- **Location:** `engine/app/jobs.py:427-433`
- **Severity:** low
- **Confidence:** **Verified by reading the surrounding code.** The semaphore limits entry into `_run_item`, but the starred generator passed to `gather` eagerly creates/schedules an awaitable for every pending item.
- **What breaks:** A batch with a very large number of inputs consumes memory proportional to the whole batch before useful work completes; the semaphore does not bound queued task allocation.
- **Concrete trigger:** Create an `each`-mode job containing tens or hundreds of thousands of input records. Line 433 schedules all guarded workers concurrently, with most waiting on the semaphore.

### 9. Tar extraction is traversal-safe only on runtimes supporting `filter="data"`

- **Location:** `engine/app/installer.py:237-241`
- **Severity:** high on Python < 3.12; not applicable on a runtime where the filter is supported
- **Confidence:** **Suspicion/compatibility finding, not verified against the packaged runtime version.** The fallback explicitly calls unrestricted `extractall()`.
- **What breaks:** On a runtime that raises `TypeError` for the filter argument, a checksum-approved tar containing `../...` or an absolute member can write outside the staging directory. The checksum reduces exposure to the configured upstream artifact, but does not make the extraction primitive safe if a pinned artifact or manifest is compromised.
- **Concrete trigger:** Run on Python < 3.12 and install a configured tar source whose valid expected checksum corresponds to an archive containing a member such as `../../outside`. The fallback extracts it without path validation.

### 10. Cancellation sets in the installer have the same check/set race

- **Location:** `engine/app/installer.py:103-105, 550-551, 600-607`
- **Severity:** low
- **Confidence:** **Verified by reading the surrounding code.** Membership checks, additions, and discards occur outside `_lock` after status was checked inside it.
- **What breaks:** A late cancel can be accepted based on `running`, race after the worker's final discard, and leave the pack ID permanently in `_cancelled`. A subsequent install calls `discard` at line 592, so the leak lasts until reinstall or process exit; status returned by cancellation can also fail to reflect the request.
- **Concrete trigger:** Let `cancel()` pass line 604, then let the worker complete and discard at line 551 before `cancel()` adds at line 606.

### Route-planning loop assessment

No looping defect was found in the reviewed planner. `find_route()` bounds paths by `max_hops` (`engine/app/registry.py:347-350`) and uses a `seen` format set (`345, 361, 369`), so cyclic converter registrations do not enqueue a previously visited format indefinitely. This conclusion is **verified from the surrounding implementation**, not a claim that route selection is globally optimal.

## Checks and modified files

- Modified: `REVIEW_CODEX.md` only.
- Checks: static, line-numbered inspection with `rg`, `nl`, and `sed`; repository call-site searches for job cleanup and downloader/installer entry points. The application and converters were not run.
