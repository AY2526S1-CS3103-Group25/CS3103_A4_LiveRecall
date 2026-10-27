# AI / Coding-Assistant Usage

## How AI was used

### Issue #3 - live caption display

AI helped interpret the requirements, plan and implement caption revision
replacement, distinguish provisional and final captions, and improve comments
and documentation. It also assisted with checking the display behavior.

## One useful suggestion

AI suggested keeping one caption row per segment and updating it when a newer
revision arrives. This prevents duplicate lines. Browser checks confirmed that
newer revisions replace existing text, older updates are ignored, and final
captions remain protected.

## One suggestion that required modification

AI suggested testing with the real caption source and server, but the execution
environment blocked local server ports. Controlled caption messages were used
to check the browser instead. This verified the display logic, but a separate
test with the real source and server is still needed to confirm the full flow.

## Responsibility statement

We understand that the team remains responsible for the submitted code, experimental results, and claims, and that every member may be asked to explain or modify relevant code during the viva.
