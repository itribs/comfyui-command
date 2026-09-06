const OUTPUT_MAX_LINES = 500;

export function installCommandOutputPanel(node) {
    if (node.commandOutputWrapper) return;

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

    node.addDOMWidget("command_output", "custom", wrapper, {
        getValue: () => "",
        setValue: () => {}
    });

    const cmdWidget = node.widgets ? node.widgets[node.widgets.length - 1] : null;
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

    node.commandOutputWrapper = wrapper;
    node.commandOutputEl = outputEl;

    node.appendCommandOutput = (text) => {
        const outputEl = node.commandOutputEl;
        if (!outputEl) return;
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
}