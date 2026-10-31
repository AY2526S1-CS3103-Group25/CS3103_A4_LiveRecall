# LiveRecall Protocol Examples

These examples illustrate message flow only. They do not prescribe an internal server design.

## 1. Normal connection

```text
Client -> Server: CLIENT_HELLO(last_segment=0, last_revision=0, history_seconds=30)
Server -> Client: HISTORY(items=[...])       # may be empty
Source -> Server: CAPTION_UPDATE(segment=1, revision=1, final=false, ...)
Server -> Client: CAPTION_UPDATE(...)
Source -> Server: CAPTION_UPDATE(segment=1, revision=2, final=false, ...)
Server -> Client: CAPTION_UPDATE(...)
Source -> Server: CAPTION_UPDATE(segment=1, revision=3, final=true, ...)
Server -> Client: CAPTION_UPDATE(...)
```

The browser should replace older revisions of the same segment rather than display three duplicate lines.

## 2. Temporary disconnect and resume

Suppose the client has finalized segment 38 revision 3 before disconnecting.

```text
Client disconnects
... source continues ...
Client reconnects
Client -> Server:
  CLIENT_HELLO(last_segment=38, last_revision=3, history_seconds=30)
Server -> Client:
  HISTORY(items=[recent finalized captions the client genuinely missed])
... live CAPTION_UPDATE messages continue ...
```

`last_segment` / `last_revision` describe the latest **finalized** state already held by the client, not the latest provisional update it happened to see.

## 3. Late join

A brand-new client uses:

```json
{
  "type": "CLIENT_HELLO",
  "client_id": 17,
  "session": "L1",
  "last_segment": 0,
  "last_revision": 0,
  "history_seconds": 30
}
```

The server provides recent finalized context available inside the bounded history window, then the client continues with live updates.

## 4. Large recovery windows (server extension)

The server normally replies with one HISTORY message. If the selected snapshot
would exceed the load client's 1 MiB receive limit, it sends successive HISTORY
messages, each within that limit. Items stay in segment order across batches;
clients merge each batch rather than replacing the previous batch. No retained
final is discarded to make the snapshot fit one message.

A single caption may fit the receive limit while its HISTORY wrapper would not.
In that case it uses the existing CAPTION_UPDATE format during recovery, after
an initial HISTORY reply. Both paths use the same revision/final deduplication
rules. The complete recovery snapshot precedes updates accepted after the hello.
The server's 70-second retention window and resume-cursor filtering are unchanged.
