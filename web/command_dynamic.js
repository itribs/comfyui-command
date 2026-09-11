/**
 * RB_Command - Dynamic inputs (strictly aligned with Impact Pack MakeImageBatch) + streaming output
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
import { getNode } from "./graph_utils.js";
import { installCommandOutputPanel } from "./command_panel.js";

const INPUT_MAX = 20;

app.registerExtension({
    name: "comfyui.rb.Command",

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

            const node = getNode(app, nodeId);
            if (!node) return;
            // For sub-workflow wrappers, install the output panel on the fly
            if (!node.commandOutputEl) {
                installCommandOutputPanel(node);
            }
            if (node.commandOutputEl) {
                if (data === "[CLEAR]") {
                    node.commandOutputEl.innerHTML = "";
                } else {
                    node.appendCommandOutput(data);
                }
            }
        });

        // When user opens a sub-workflow, scroll all command outputs to bottom
        app.canvas?.canvas?.addEventListener("subgraph-opened", () => {
            requestAnimationFrame(() => {
                document.querySelectorAll(".command-output").forEach(el => {
                    el.scrollTop = el.scrollHeight;
                });
            });
        });
    },

    beforeRegisterNodeDef(nodeType, nodeData, app) {
        if (nodeData.name !== "RB_Command" && nodeData.name !== "RB_CommandInputBundle") return;
        const isBundleNode = nodeData.name === "RB_CommandInputBundle";

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

            if (!isBundleNode) {
                const commandWidget = this.widgets?.find(w => w.name === "command");
                if (commandWidget) {
                    commandWidget.tooltip =
                        "Placeholders: {input0} {input0_0} {input0_count} {input_count} {input_all} {input_images} {input_videos} {input_audios} {input_image_count} {input_video_count} {input_audio_count} {seed}\n" +
                        "Environment variables: $INPUT_0 $INPUT_0_0 $INPUT_1 $SEED $INPUT_COUNT $INPUT_ALL $INPUT_IMAGES $INPUT_VIDEOS $INPUT_AUDIOS $INPUT_IMAGE_COUNT $INPUT_VIDEO_COUNT $INPUT_AUDIO_COUNT";
                }

                this.addCommandOutputPanel();
            }

            return r;
        };

        // ---------------- Output panel ----------------
        nodeType.prototype.addCommandOutputPanel = function () {
            installCommandOutputPanel(this);
        };
    },
});