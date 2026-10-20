# Conversations with working models

Studio's **Conversations** view connects visible conversation to a profile's
actual OpenCode working session. The first version supports local
JobBench/OpenCode profile experiments on **Windows with one domain worker**. Conversation
during work is optional and off by default. Existing experiments keep their
usual behavior unless explicitly started with it enabled.

Conversation-enabled benchmark runs require Windows process containment in this
version. POSIX nested-worker cancellation is not implemented for this bridge;
those starts are rejected before creating run or profile records. Archive
viewing and standalone profile chat remain available. Native model/session
lifecycle validation described here was performed on Windows.

This is ordinary conversation, not an approval queue. You can ask what the model
is doing, discuss an idea, or say something casual. A message is not automatically
an instruction to change the task, a permanent memory, or a harness improvement.

## Start deliberately

In **Profiles**, **Talk to [name]** creates a conversation from that profile's
active configuration and harness, then starts its model connection. In
**Conversations**, **A new conversation** only saves the starting point. Use
**Start conversation** separately when you want to connect. Opening a saved
conversation or reading its messages never loads a model.

A managed local model loads from the conversation's recorded configuration.
An external local server must already be running; Studio connects to it without
taking ownership. There is one active run or idle conversation at a time.

To talk during an experiment, choose a local profile in **New experiment**,
select JobBench and OpenCode, set **Parallel domains** to one, and check
**Conversation during work**. The run's detail links to its conversation.
Solver and proposer sessions take turns; they are not one undifferentiated
context. The interface identifies the active worker, its native session, and
earlier working contexts. Each message retains the worker to which it belonged.

Only an active worker can receive a message. While a run changes stages, the
conversation may wait for its next worker. This does not start another model
to answer on the worker's behalf. A model can also initiate visible contact
through its conversation tool in the same native session.

## Delivery is recorded

Visible messages have explicit states:

| State | What it records |
| --- | --- |
| Queued | Saved for a particular worker, awaiting delivery. |
| Delivered to worker | Submitted to that worker's native session. |
| Answered | A visible answer was linked to the delivered message. |
| Not delivered | Delivery was not confirmed, or the worker ended before delivery; the reason is retained. |
| Failed | Delivery happened, but the worker ended or failed before an answer. |

A queued or delivered message is not presented as an answer. After interruption
or a context switch, undelivered messages are not silently replayed to another
worker. Drafts stay in the current open page while the selected conversation
is polled; they are separate from messages that have been sent.

An accepted request can reach the native session even when its receipt cannot
be confirmed. Such a message keeps an explicit unconfirmed-delivery reason and
is not automatically resent. Provenance records confirmed delivery separately
from possible human contact; an unconfirmed attempt does not establish that the
model worked without human contact.

## Continue without leaving a model loaded

During an active experiment, use **Follow run / stop work** and **Stop run**.
The conversation does not independently stop the model underneath that run.
When an idle conversation is connected, **Stop chat** closes its session
connection and unloads any model server it owns. External servers remain under
their existing owner's control.

An idle conversation also disconnects after ten minutes without activity.
Active model work extends that interval. Closing Studio cleans up its owned
conversation processes and model server. Closing only the browser tab leaves
Studio running, so the inactivity timer still applies.

History and native session files remain on disk. **Continue chat** explicitly
reloads or reconnects the recorded model and resumes the retained native
session. Configuration or session identity mismatches fail instead of silently
substituting a new context. Continuation after a run uses that conversation's
retained working context; it does not combine every earlier worker's full
history into one session.

## Four separate records

The stores serve different purposes:

| Record | Purpose and location |
| --- | --- |
| Visible conversation | Sent messages, answers, delivery states, and workers in `.harness2/conversations/<id>/history/`. |
| Working continuity | Sourced decisions, recovery checkpoints, and observations in `.harness2/conversations/<id>/working/`. |
| Conversational memory | A separate reserved archive under `.harness2/conversational-memory/`. The initial Studio interface and model tools do not populate it. |
| Technical profile | Configurations, harness revisions, evidence, and adoption in `.harness2/profiles/`. Chat does not automatically rewrite it. |

**Working continuity** shows the objective, constraints, reported completed
work, planned work, open questions, and consequential decisions. Reported
completion and any supplied references are not independently verified. A
decision can retain source-message IDs and a reason, including a later revision
or reversal. Its source links let you inspect the actual exchange.

Decision tools use the conversation's `source_message_id` supplied with a human
message, rather than the native transport's `msg_...` identifier. When a recorded
decision changes a proposed harness, the model declares it on a standalone line
in the selected stage's final notes:

```text
CONVERSATION_DECISION: <full 32-hex decision ID>
```

Incidental mentions embedded in prose do not count as this standalone declaration.

This records a declared connection between conversation and a decision. It does
not establish that the advice improved the result. Run provenance likewise
records human contact without treating contact as a measured benefit. A working
decision labelled adopted is not adoption of a technical harness; profile
activation remains a separate choice.

Checkpoints help working continuity survive compaction, but neither recording a
checkpoint nor retaining a transcript proves that a model recovers correctly.
The visible conversation excludes hidden reasoning and raw provider traffic.

## Retained session data

Private runtime material lives under `.harness2/conversation-runtime/<id>/`:
the pinned starting snapshot, native OpenCode data and state, session identity,
and retained working directories. These files enable continuation; they consume
disk space after model RAM has been released. Native session data can contain
task material and internal runtime records beyond the visible conversation.
It is not exported as public conversation history or copied into profile
experience.

Back up the conversation archive and its matching private runtime together if
you need native continuation, along with the relevant profile and run folders.
The UI does not yet provide session pruning. Removing private session files can
make continuation unavailable even when the visible history remains readable.

See [the Studio guide](UI.md) and [model profiles](PROFILES.md). The
[validation record](VALIDATION.md) distinguishes deterministic fixtures from
actual model-backed execution.
