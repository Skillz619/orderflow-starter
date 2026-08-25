# Written answers

Copy this file to `ANSWERS.md`, fill it in, and include it in your submission.
A few sentences per question is plenty — we're reading for how you think,
not length.

## 1. Cardinality

Suppose a teammate adds `user_id` as a label on a Prometheus counter that
tracks API requests, on a service with 2 million users. What happens to
that metric over the life of the service, and what would you suggest
instead if per-user request counts are genuinely needed?

# Answer
Prometheus creates a new time series for every unique combination of label values, and it keeps all of them in memory, so `user_id` on a service with 2M users means that counter could balloon to millions of series — memory climbs, queries against it get slow, and it can drag down Prometheus for everyone else too. If per-user counts are actually needed I wouldn't put `user_id` on a metric label at all — I'd log it or attach it as a span attribute instead, and if it has to be a metric, bucket users into something small like plan tier instead of the raw ID.


## 2. p99 vs. average

A dashboard shows average request latency holding steady at 80ms, but
users are filing tickets about the app "randomly" feeling slow. What
might the average be hiding, and what would you look at instead?

# Answer
Averages hide outliers — a handful of really slow requests get washed out by a much larger number of fast ones, so an 80ms average could mean most requests are 20-30ms while some smaller chunk is taking 500ms+, and that chunk is what people are actually hitting and complaining about. I'd check p95/p99 instead of the mean, look at whether that tail is growing or tied to specific endpoints/hours, and pull a few real traces from the p99 bucket to see exactly where the time is going.


## 3. Streaming / backpressure

You're exporting spans from a fleet of services to a Collector gateway
over OTLP, and the gateway's downstream exporter (say, a tracing backend)
starts throttling requests. Walk through what should happen next in the
pipeline, and what could go wrong if buffering and retry aren't handled
carefully.

# Answer
If the downstream exporter starts throttling, the Collector needs to back off instead of just firing requests at it — retry with backoff and buffer what it can't send yet rather than dropping it immediately, which the batch processor already helps with. The failure mode is an unbounded buffer: if the Collector just keeps queueing everything while the backend is slow it'll eventually run out of memory and crash, taking the already-queued spans down with it, so you want a bounded queue that drops the oldest/excess data past a point — some data loss under sustained backpressure is fine, losing the whole pipeline isn't.


## 4. Instrumenting agent workflows

`/agent/analyze` runs a plan -> tool_call -> model_inference pipeline —
similar in shape to real agentic systems that call external tools and
LLMs. If you were designing the instrumentation approach for a whole
codebase full of workflows like this (not just one endpoint), what would
you standardize so that every workflow's cost and latency show up
consistently, without every engineer hand-rolling their own spans?

# Answer
I'd want a shared wrapper/decorator every workflow uses instead of engineers hand-writing spans — something like `@traced_step("plan")` that handles starting/ending the span, setting status codes, and propagating context correctly, since that's an easy thing to get wrong every time otherwise (like the thread-hop issue in this exercise). I'd also standardize attribute names across workflows — token counts, model name, step name should be the same keys everywhere, not `llm.tokens` in one service and `token_count` in another — since that's what actually makes cost/latency roll up consistently instead of every team building their own dashboard with their own naming.


## 5. Kubernetes DaemonSet

`k8s/daemonset-broken.yaml` deploys a node-level telemetry agent as a
DaemonSet. It has real problems — at least one that stops it from
scheduling at all, and at least two more that are silent (it runs, it
looks healthy, and it's still wrong). Fix the manifest, then list what
you found and why each one matters. You don't need a running cluster
for this.

# Answer
Found three problems: (1) **fatal** — the selector said `app: node-telemetry` but the pod template said `app: node-telemetry-agent`, and Kubernetes rejects the DaemonSet outright if those don't match, so nothing ever schedules; (2) **silent** — no tolerations for control-plane taints, even though the header comment explicitly says this should cover control-plane nodes too, so it quietly skips them while every pod that does schedule looks perfectly healthy in `kubectl get pods`; (3) **silent** — no resource requests, which gives the pod Best-Effort QoS, the first thing kubelet evicts under memory pressure, meaning the telemetry agent disappears exactly when the node is struggling and you need it most. Fixed all three, and also made the hostPath mounts readOnly with mountPropagation set since nothing here should write to host /proc or /sys and new host mounts wouldn't otherwise show up in the container.


## 6. Bare-metal / BMC

`bmc/bmc_thermal_task.py` and `bmc/redfish_thermal_sample.json` are a
small Redfish `Thermal` payload and a stub to parse it. Implement
`parse_redfish_thermal()`, then answer: why is server hardware telemetry
like this typically pulled out-of-band via Redfish/IPMI rather than from
an agent running on the host OS, and when would you expect to fall back
from Redfish to IPMI on real hardware?

# Answer
Redfish/IPMI is out-of-band because it talks to the BMC, a separate little computer on the motherboard with its own power and network connection, so it keeps working even if the host OS is crashed, hung, or powered off — which is the whole point, since an agent on the host OS is useless if the host OS is the thing that's down. I'd expect to fall back to IPMI on older hardware that predates Redfish, or on cheaper/lower-end BMCs that never implemented a full Redfish API even on newer boards.


## 7. Linux fundamentals across a mixed fleet

Say the same exporter needs to run as a native systemd service (not a
container) across a fleet that's a mix of CentOS and Ubuntu hosts —
same unit file, same binary. Name two concrete differences between the
two distros that could make that exact setup behave differently, or
fail silently, on one but not the other.

# Answer
Two things that'd bite you: SELinux is enforcing by default on CentOS/RHEL in a lot of setups, so a binary that runs fine on Ubuntu can get silently blocked with nothing obvious in the systemd logs unless you specifically check `journalctl` plus audit logs; and CentOS ships noticeably older glibc/OpenSSL than a recent Ubuntu LTS, so a binary built against a newer glibc can fail to even start with a version mismatch error, or a TLS negotiation can behave differently because of the OpenSSL gap.


## 8. Diagnosing a slow node

You SSH into a bare-metal node that's reportedly "slow," and there's no
monitoring stack installed yet — no Prometheus, no exporters, nothing.
What are the first 3-4 native Linux commands you'd reach for, and what
would each one tell you?

# Answer

`top`/`htop` first for a quick read on whether it's CPU or memory bound and which process is eating resources, `iostat -x 1` to check disk since high `%util`/await times point to an I/O bottleneck instead, `free -h` for a clearer memory picture especially how much is in swap (a very common cause of "randomly slow"), and `dmesg | tail` to check for OOM killer activity or kernel-level hardware errors that top/iostat won't show at all.


# What I'd do differently with more time:
Biggest gap is I never wired up a real backend — Jaeger or Tempo — so I was reading raw spans out of Collector logs instead of an actual trace viewer, which is fine for proving the pipeline works but not how I'd actually debug this day to day. I'd also want real tests around the Redfish parser's edge cases instead of just checking it against one sample payload
