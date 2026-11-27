/* Bundled PCV navigation adapter for HA frontend 20260930.2 (Core 2026.10).
 * No imports, network dependencies, prototype patches or changes to other flows.
 * Only public step properties, HA buttons and the native flow-update event are used.
 * HA does not expose a supported custom secondary-action slot to OptionsFlow authors;
 * keep this small DOM adapter covered by actual frontend release checks.
 */
const DOMAIN = "polish_context_voice";
const attached = new WeakSet();

function findDialogs(root, found = []) {
  for (const element of root.querySelectorAll("*")) {
    if (element.localName === "dialog-data-entry-flow") found.push(element);
    else if (element.shadowRoot) findDialogs(element.shadowRoot, found);
  }
  return found;
}

function attach(dialog) {
  if (attached.has(dialog) || !dialog.shadowRoot) return;
  attached.add(dialog);
  let pending = false;
  let repeat = false;
  const watched = new WeakSet();
  const observe = root => {
    if (!root || watched.has(root)) return;
    watched.add(root);
    new MutationObserver(refresh).observe(root, { childList: true, subtree: true });
  };
  let active;
  let busy = false;
  const refresh = async () => {
    if (pending) { repeat = true; return; }
    pending = true;
    try {
      const host = dialog.shadowRoot.querySelector("step-flow-form, step-flow-menu");
      const clear = () => {
        dialog.shadowRoot.querySelectorAll("[data-pcv-back], [data-pcv-footer]").forEach(node => node.remove());
        active = undefined;
      };
      if (!host) { clear(); return; }
      await host.updateComplete;
      observe(host.shadowRoot);
      const step = host.step;
      const raw = step?.description_placeholders?.pcv_navigation;
      if (!raw || host.flowConfig?.flowType !== "options_flow") { clear(); return; }
      const nav = JSON.parse(raw);
      if (nav.domain !== DOMAIN) return;
      active = { host, step, nav };
      const container = dialog.shadowRoot.querySelector("ha-dialog");
      if (!container) return;
      const own = container.querySelector("[data-pcv-footer]");
      const native = container.querySelector("ha-dialog-footer:not([data-pcv-footer])");
      if (own && native) own.remove();
      let footer = native || own;
      if (!footer) {
        footer = document.createElement("ha-dialog-footer");
        footer.slot = "footer";
        footer.setAttribute("data-pcv-footer", "");
        container.append(footer);
      }
      let button = footer.querySelector("[data-pcv-back]");
      if (!button) {
        button = document.createElement("ha-button");
        button.setAttribute("data-pcv-back", "");
        button.slot = "secondaryAction";
        button.setAttribute("appearance", "outlined");
        footer.prepend(button);
        button.addEventListener("click", () => {
          const current = active;
          if (busy || !current?.host.isConnected) return;
          busy = true;
          button.disabled = true;
          current.host.inert = true;
          const next = footer.querySelector('[slot="primaryAction"]');
          if (next) next.disabled = true;
          // Ignore the edited draft, including invalid/empty fields. Defaults and
          // permission checks are applied by the native server-side flow manager.
          const promise = current.host.flowConfig.handleFlowStep(
            current.host.hass, current.step.flow_id, current.nav.data
          );
          current.host.dispatchEvent(new CustomEvent("flow-update", {
            bubbles: true, composed: true, detail: { stepPromise: promise },
          }));
          Promise.resolve(promise).finally(() => { busy = false; }).catch(() => {});
        });
      }
      const polish = host.hass?.locale?.language?.startsWith("pl");
      const label = polish ? "Wróć" : "Back";
      if (button.textContent !== label) button.textContent = label;
      button.title = step.step_id === "voice_review"
        ? (polish ? "Wróć do edycji" : "Return to editing")
        : (polish ? "Wróć bez zapisywania tego formularza" : "Go back without saving this form");
      button.disabled = busy;
      if (host.localName === "step-flow-form") {
        const form = host.shadowRoot?.querySelector("ha-form");
        if (form) {
          await form.updateComplete;
          observe(form.shadowRoot);
        }
        // Hide only the boolean control, leaving ha-form.schema untouched.
        // Replacing the schema would recreate HA pickers and close open lists
        // whenever live HA state updates rerender the parent form.
        for (const field of form?.shadowRoot?.querySelectorAll("ha-form-boolean") || []) {
          if (field.schema?.name === "back") field.style.setProperty("display", "none", "important");
        }

      } else if (Array.isArray(step.menu_options) &&
          step.menu_options.includes(nav.data.next_step_id)) {
        host.step = { ...step,
          menu_options: step.menu_options.filter(option => option !== nav.data.next_step_id),
        };
      }
    } catch (error) {
      console.warn("PCV: navigation adapter unavailable; native navigation retained", error);
    } finally {
      pending = false;
      if (repeat) { repeat = false; queueMicrotask(refresh); }
    }
  };
  observe(dialog.shadowRoot);
  dialog.addEventListener("flow-update", () => { active = undefined; });
  dialog.addEventListener("flow-step-footer-state-changed", event => {
    const button = dialog.shadowRoot.querySelector("[data-pcv-back]");
    if (button) button.disabled = busy || Boolean(event.detail.loading);
  });
  refresh();
}

// Discovery is bounded and runs only when opening our dialog. Afterwards, only
// that dialog is observed; there is no polling or whole-page mutation observer.
window.addEventListener("show-dialog", async event => {
  const params = event.detail;
  if (params?.dialogTag !== "dialog-data-entry-flow" ||
      params.dialogParams?.domain !== DOMAIN) return;
  await customElements.whenDefined("dialog-data-entry-flow");
  for (let attempt = 0; attempt < 8; attempt++) {
    const dialogs = findDialogs(document);
    dialogs.forEach(attach);
    if (dialogs.some(dialog => attached.has(dialog))) return;
    await new Promise(resolve => setTimeout(resolve, 75));
  }
});
findDialogs(document).forEach(attach);
