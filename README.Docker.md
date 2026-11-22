### Building and running your application

When you're ready, start your application by running:
`docker compose up --build`.

Your application will be available at http://localhost:5959.

### Download progress and recovery

- When Herbie's 120-second availability check times out, the same forecast
  hour is checked again indefinitely; availability timeouts do not consume
  the failure budget. The stop button can interrupt between checks.
- Other availability errors, download errors and invalid GRIB files count
  toward four failed attempts, with delays of 60, 120 and 180 seconds. After the fourth
  failure, the forecast hour is skipped and logged; the most recent skipped
  hour is also displayed on the status page and persisted. The status page
  button clears this indication without changing the saved download position
  or deleting files. A later abandoned hour will be displayed normally.
- The configuration under `~/.config/gribextractor/config.txt` records the next
  forecast hour to process after a successful download or a skipped hour. It is
  stored in the mounted home directory. On first upgrade from the older format,
  the last saved hour is processed again because its completion cannot be inferred
  reliably from that format.
- An unexpected worker error is logged and the worker is restarted after
  30 seconds from the saved checkpoint. Persistent errors, such as a corrupted
  configuration or an unwritable directory, must be corrected manually.
- The stop button responds immediately. Retry and run-start waits are
  interruptible, but an active Herbie availability check or download must finish
  before the worker actually stops. The status page shows when stopping is in
  progress; restarting is disabled until then.
- The status page counts validated downloads by successful attempt and abandoned
  forecast hours. Percentages use all completed outcomes (validated downloads
  plus abandoned hours) as their denominator. Timeouts waiting for availability
  do not count as failed attempts. Clearing the last-abandoned indication does
  not reset the statistics. Counters are
  kept in memory: they survive stopping and restarting the worker but reset
  when the container restarts. Reprocessing a file counts as another download.

### Source priority

Set `PRIORITY` to an ordered, comma-separated list of Herbie source names in
`compose.yaml`, for example `PRIORITY=aws,google,azure` (the default).
Whitespace around names is ignored and names are converted to lowercase.
Empty entries and unknown names prevent startup. Accepted GFS sources are
`aws`, `google`, `azure`, `nomads` and `ncar_rda`; the latter does not
provide an index for partial downloads.

### Diagnosing a blocked worker on Linux

The application registers a `SIGUSR1` handler at startup. It writes the Python
stacks of all threads to the container's standard error without stopping them.
In an SSH session on the Docker host, first confirm that the startup log says
`Diagnostic des threads activé : signal SIGUSR1`:

```sh
docker logs gribextractor 2>&1 | grep 'Diagnostic des threads activé'
```

Only after verifying that the running container has this handler, capture the
status and request two stack dumps about 30 seconds apart:

```sh
curl -s http://localhost:5959/statut
docker kill --signal=USR1 gribextractor
sleep 30
docker kill --signal=USR1 gribextractor
docker logs --timestamps --since 5m gribextractor 2>&1
```

Despite the command name, `docker kill --signal=USR1` only sends the specified
signal; the registered handler does not stop the container. **Do not omit
`--signal=USR1`**, and do not send it to an older container that has not
registered the handler: either could terminate the application. Review logs
for sensitive information before sharing them.

### Deploying your application to the cloud

First, build your image, e.g.: `docker build -t myapp .`.
If your cloud uses a different CPU architecture than your development
machine (e.g., you are on a Mac M1 and your cloud provider is amd64),
you'll want to build the image for that platform, e.g.:
`docker build --platform=linux/amd64 -t myapp .`.

Then, push it to your registry, e.g. `docker push myregistry.com/myapp`.

Consult Docker's [getting started](https://docs.docker.com/go/get-started-sharing/)
docs for more detail on building and pushing.

### References
* [Docker's Python guide](https://docs.docker.com/language/python/)