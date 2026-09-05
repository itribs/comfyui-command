/**
 * CommandNode - Dynamic inputs (strictly aligned with Impact Pack MakeImageBatch) + streaming output
 *
 * Alignment notes:
 * 1. Synchronous add/remove inside onConnectionsChange, no defer
 * 2. Disconnect removal guarded by call stack: never delete when called from user drag (LGraphNode.connect)
 * 3. loadGraphData / pasteFromClipboard only updates state, does not touch slots
 * 4. Renumber all input_N slots in order after each event (links use index, renaming is safe)
 * 5. Sync addInput on connection
 * 6. New frontend widgets may mix into inputs, all traversal must filter by name prefix
 */
import { app } from "/scripts/app.js";
import { api } from "/scripts/api.js";

const INPUT_MAX = 20;
const OUTPUT_MAX_LINES = 500;

app.registerExtension({
    name: "comfyui.CommandNode",

    async setup() {
        // Load external CSS
        if (!document.getElementById("command-node-style-link")) {
            const link = document.createElement("link");
            link.id = "command-node-style-link";
            link.rel = "stylesheet";
            link.type = "text/css";
            link.href = new URL("command_style.css", import.meta.url).href;
            document.head.appendChild(link);
        }

        // Listen for backend streaming output
        api.addEventListener("command_node_output", (event) => {
            const { node: nodeId, data } = event.detail;
            const node = app.graph.getNodeById(Number(nodeId));
            if (node && node.commandOutputEl) {
                if (data === "[CLEAR]") {
                    node.commandOutputEl.innerHTML = "";
                } else {
                    node.appendCommandOutput(data);
                }
            }
        });
    },

    beforeRegisterNodeDef(nodeType, nodeData, app) {
        if (nodeData.name !== "CommandNode") return;

        const onConnectionsChange = nodeType.prototype.onConnectionsChange;
        nodeType.prototype.onConnectionsChange = function (type, index, connected, link_info) {
            const orig = onConnectionsChange?.apply(this, arguments);
            const stackTrace = new Error().stack;

            // HOTFIX: subgraph
            if (stackTrace.includes('convertToSubgraph') || stackTrace.includes('Subgraph.configure')) {
                return orig;
            }

            // Load workflow / paste: don't touch slots
            if (stackTrace.includes('loadGraphData') || stackTrace.includes('pasteFromClipboard')) {
                return orig;
            }

            if (!link_info) return orig;
            if (type == 2) return orig;   // output port, ignore

            const isCommandSlot = (s) => /^input_\d+$/.test(s?.name || "");

            // Key fix: only remove empty slots from the tail, and always keep 1 empty slot.
            //   Never do mid-array removeInput — otherwise the target_slot
            //   index of subsequent slots shifts, causing other connections to be invalidated by the frontend (root cause of "disconnect 1" bug)
            if (!connected) {
                // Shrink from tail: remove excess trailing empty slots (keep at most one empty slot)
                let emptyTail = 0;
                for (let i = this.inputs.length - 1; i >= 0; i--) {
                    const s = this.inputs[i];
                    if (!isCommandSlot(s)) continue;
                    if (s.link == null) emptyTail++;
                    else break;
                }
                // emptyTail = consecutive trailing empty slots; keep 1, remove the excess
                for (let k = 0; k < emptyTail - 1; k++) {
                    // Remove the last input slot each iteration
                    let lastIdx = -1;
                    for (let i = this.inputs.length - 1; i >= 0; i--) {
                        if (isCommandSlot(this.inputs[i])) { lastIdx = i; break; }
                    }
                    if (lastIdx > 0 && this.inputs[lastIdx].link == null) {
                        this.removeInput(lastIdx);   // tail removal, no index shift risk
                    }
                }
            }

            // Renumber: name all input_N slots in order (renaming doesn't affect links, which use index)
            let slot_i = 0;
            for (let i = 0; i < this.inputs.length; i++) {
                const input_i = this.inputs[i];
                if (isCommandSlot(input_i)) {
                    input_i.name = `input_${slot_i}`;
                    input_i.label = `Input ${slot_i}`;
                    input_i.color = "#9b59b6";
                    slot_i++;
                }
            }

            // On connection: sync-add a new empty slot (ensures there's always one empty slot available)
            if (connected && slot_i < INPUT_MAX) {
                // Don't add if there's already an empty slot at the tail
                const lastCmd = [...this.inputs].reverse().find(isCommandSlot);
                if (!lastCmd || lastCmd.link != null) {
                    this.addInput(`input_${slot_i}`, "*");
                    const s = this.inputs[this.inputs.length - 1];
                    s.color = "#9b59b6";
                    s.label = `Input ${slot_i}`;
                }
            }

            return orig;
        };

        const onNodeCreated = nodeType.prototype.onNodeCreated;
        nodeType.prototype.onNodeCreated = function () {
            const r = onNodeCreated?.apply(this, arguments);

            const commandWidget = this.widgets?.find(w => w.name === "command");
            if (commandWidget) {
                commandWidget.tooltip =
                    "Placeholders: {input0} {input_count} {input_all} {input_image_count} {input_video_count} {input_audio_count} {seed}\n" +
                    "Environment variables: $INPUT_0 $INPUT_1 $SEED $INPUT_COUNT $INPUT_ALL $INPUT_IMAGE_COUNT $INPUT_VIDEO_COUNT $INPUT_AUDIO_COUNT";
            }

            this.addCommandOutputPanel();

            return r;
        };

        // ---------------- Output panel ----------------
        nodeType.prototype.addCommandOutputPanel = function () {
            if (this.commandOutputWrapper) return;

            const wrapper = document.createElement("div");
            wrapper.className = "command-output-wrapper";

            const header = document.createElement("div");
            header.className = "command-output-header";
            header.innerHTML = `
                <span class="cn-title">Command Output</span>
                <span class="cn-actions">
                    <span class="cn-copy" title="Copy output">⧉</span>
                    <span class="cn-copy-ok">✓</span>
                </span>
            `;

            const outputEl = document.createElement("div");
            outputEl.className = "command-output";

            const copyBtn = header.querySelector(".cn-copy");
            const copyOk = header.querySelector(".cn-copy-ok");

            copyBtn.addEventListener("click", (e) => {
                e.stopPropagation();
                const text = Array.from(outputEl.children)
                    .map(el => el.textContent)
                    .join("\n");
                navigator.clipboard.writeText(text).then(() => {
                    copyBtn.style.display = "none";
                    copyOk.style.display = "inline-flex";
                    setTimeout(() => {
                        copyOk.style.display = "none";
                        copyBtn.style.display = "inline-flex";
                    }, 1500);
                }).catch(() => {});
            });

            wrapper.appendChild(header);
            wrapper.appendChild(outputEl);

            this.addDOMWidget("command_output", "custom", wrapper, {
                getValue: () => "",
                setValue: () => {}
            });

            const cmdWidget = this.widgets ? this.widgets[this.widgets.length - 1] : null;
            if (cmdWidget) {
                cmdWidget.computeSize = function () {
                    return [this.size?.[0] || 0, wrapper.offsetHeight + 20];
                };
            }

            const handle = document.createElement("div");
            handle.className = "cn-resize-handle";
            let startY = 0, startH = 0;
            handle.addEventListener("mousedown", (e) => {
                e.preventDefault();
                startY = e.clientY;
                startH = wrapper.offsetHeight;
                const onMove = (ev) => {
                    const newH = Math.max(100, startH + (startY - ev.clientY));
                    wrapper.style.height = newH + "px";
                    if (cmdWidget) cmdWidget.size[1] = newH;
                };
                const onUp = () => {
                    document.removeEventListener("mousemove", onMove);
                    document.removeEventListener("mouseup", onUp);
                };
                document.addEventListener("mousemove", onMove);
                document.addEventListener("mouseup", onUp);
            });
            wrapper.insertBefore(handle, outputEl);

            this.commandOutputWrapper = wrapper;
            this.commandOutputEl = outputEl;

            this.appendCommandOutput = (text) => {
                const wasAtBottom = outputEl.scrollHeight - outputEl.scrollTop - outputEl.clientHeight < 5;

                if (text.startsWith("\x01")) {
                    text = text.slice(1);
                    const lastLine = outputEl.lastElementChild;
                    if (lastLine && lastLine.classList.contains("cn-partial")) {
                        lastLine.textContent = text;
                    } else {
                        const line = document.createElement("div");
                        line.className = "command-line cn-partial";
                        line.textContent = text;
                        outputEl.appendChild(line);
                    }
                } else {
                    text = text.trim();
                    if (!text) text = "\u00A0";
                    const lastLine = outputEl.lastElementChild;
                    if (lastLine && lastLine.classList.contains("cn-partial")) {
                        lastLine.textContent = text;
                        lastLine.classList.remove("cn-partial");
                    } else {
                        const line = document.createElement("div");
                        line.className = "command-line";
                        line.textContent = text;
                        outputEl.appendChild(line);
                    }
                }
                while (outputEl.childElementCount > OUTPUT_MAX_LINES) {
                    outputEl.removeChild(outputEl.firstChild);
                }
                if (wasAtBottom) {
                    requestAnimationFrame(() => {
                        outputEl.scrollTop = outputEl.scrollHeight;
                    });
                }
            };

            const origOnExecutionStart = this.onExecutionStart;
            this.onExecutionStart = function () {
                origOnExecutionStart?.apply(this, arguments);
            };
        };
    },
});