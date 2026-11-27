const trialType = "polish_context_voice/trial";
const lightingType = "polish_context_voice/lighting";
const lightCategories = [
  ["voice_main", "Główne"], ["voice_task", "Robocze"], ["voice_ambient", "Dekoracyjne"],
];
const lightingGroups = [["", "Polecenia ogólne"], ...lightCategories, ["preference", "Wybór lampy"]];
const defaultChoiceCommand = "Włącz lampę";
const planKeys = (group) => group === "preference"
  ? ["preference"] : ["on", "off"].map((action) => group ? group + "_" + action : action);
const profileCategories = { main: "voice_main", task: "voice_task", ambient: "voice_ambient" };
const profileLabels = new Map(lightCategories);
profileLabels.set("voice_default_on", "Włącz razem");
profileLabels.set("voice_all_off", "Wyłącz razem");
const guardLabels = {
  voice_block: "Blokada Boćka",
  voice_exclude_all: "Pomijane w zestawach",
  voice_high_risk: "Zablokowane sterowanie",
  voice_confirm: "Wymaga potwierdzenia",
};
const lightingMessages = {
  voice_invalid_selection: "Co najmniej jedno światło jest już poza tym pokojem.",
  voice_stale: "Ustawienia zmieniły się w HA. Odrzuć szkic i wczytaj pokój ponownie.",
  voice_review_changed: "Zmienił się wynik polecenia. Sprawdź podgląd jeszcze raz.",
  voice_profile_stale: "Zestawy zmieniły się w HA. Odśwież pokój i ponów edycję.",
  voice_invalid_profile: "Wybierz co najmniej jedną rolę zestawu.",
  voice_conflict: "Co najmniej jedno wybrane światło ma blokadę sterowania grupowego.",
  voice_label_name: "Sprawdź etykietę o podobnej nazwie w ustawieniach HA.",
  voice_write_failed: "Zapis został przerwany. Wczytaj pokój i sprawdź zapisane zmiany.",
};

const template = document.createElement("template");
template.innerHTML = `
  <style>
    :host {
      display: block;
      min-height: 100%;
      color: var(--primary-text-color, #163735);
      background: var(--primary-background-color, #f5f7f6);
      font-family: var(--paper-font-body1_-_font-family, system-ui, sans-serif);
      --bociek-accent: #0c5b61;
      --bociek-border: var(--divider-color, #dfe8e5);
    }
    * { box-sizing: border-box; }
    .bar {
      height: 56px;
      display: flex;
      align-items: center;
      gap: 10px;
      padding: 0 max(16px, env(safe-area-inset-right)) 0 max(16px, env(safe-area-inset-left));
      background: var(--app-header-background-color, #fff);
      color: var(--app-header-text-color, var(--primary-text-color, #163735));
      border-bottom: 1px solid var(--bociek-border);
      font-size: 18px;
      font-weight: 650;
    }
    main { max-width: 1140px; padding: 32px 26px 52px; margin: auto; }
    .intro {
      display: flex;
      align-items: center;
      gap: 18px;
      padding: 26px 28px;
      border-radius: 16px;
      background: #0e5156;
      color: #fff;
    }
    .intro img { width: 56px; height: 56px; flex: none; border-radius: 16px; }
    .intro img:not([src]) { display: none; }
    h1, h2, h3, p { margin: 0; }
    h1 { font-size: clamp(24px, 4vw, 34px); line-height: 1.12; }
    h2 { font-size: 21px; line-height: 1.3; }
    h3 { font-size: 14px; }
    p { line-height: 1.5; }
    .intro p { color: #d1ece8; margin-top: 7px; }
    .layout { display: grid; grid-template-columns: minmax(0, 0.92fr) minmax(0, 1.08fr);
      gap: 18px; margin: 18px 0; align-items: start; }
    .card {
      padding: 24px;
      border: 1px solid var(--bociek-border);
      border-radius: 15px;
      background: var(--card-background-color, #fff);
      box-shadow: 0 2px 5px rgba(0, 40, 38, .04);
    }
    .muted { color: var(--secondary-text-color, #506966); font-size: 14px; }
    header .muted { margin-top: 5px; }
    form { display: grid; gap: 17px; margin-top: 20px; }
    .field { display: grid; gap: 6px; }
    label { font-weight: 650; }
    select, input, button, a { font: inherit; }
    select, input {
      width: 100%;
      min-height: 44px;
      padding: 9px 12px;
      border-radius: 9px;
      border: 1px solid var(--bociek-border);
      background: var(--primary-background-color, #f5f7f6);
      color: inherit;
    }
    select:focus-visible, input:focus-visible, button:focus-visible, a:focus-visible {
      outline: 2px solid var(--primary-color, #238d88);
      outline-offset: 2px;
    }
    button, .settings {
      min-height: 42px;
      border-radius: 9px;
      cursor: pointer;
    }
    button[type=submit], .primary {
      background: var(--bociek-accent);
      color: #fff;
      border: 0;
      font-weight: 650;
      padding: 8px 17px;
      justify-self: start;
    }
    button:disabled, select:disabled { cursor: default; opacity: .64; }
    .examples { display: flex; gap: 6px; flex-wrap: wrap; }
    .example, #retry {
      padding: 6px 10px;
      border: 1px solid var(--bociek-border);
      color: inherit;
      background: var(--primary-background-color, #f5f7f6);
      min-height: 36px;
      font-size: 13px;
    }
    .overview { display: grid; gap: 15px; margin-top: 16px; }
    .overview section { padding-top: 12px; border-top: 1px solid var(--bociek-border); }
    .overview section p { margin-top: 4px; }
    ul { margin: 7px 0 0; padding-left: 20px; line-height: 1.5; }
    .overview, #message { overflow-wrap: anywhere; }
    #message { margin-top: 16px; }
    #status { font-size: 15px; font-weight: 650; }
    .settings { display: inline-flex; align-items: center; text-decoration: none; padding: 0 12px;
      border: 1px solid var(--bociek-border); color: inherit; font-weight: 600; }
    .views, .lighting-controls { display: flex; gap: 8px; margin: 16px 0 0; flex-wrap: wrap; }
    .secondary { padding: 8px 14px; border: 1px solid var(--bociek-border); color: inherit;
      background: var(--card-background-color, #fff); font-weight: 600; }
    .views button[aria-pressed=true] { box-shadow: inset 0 -3px var(--primary-color, #238d88); }
    .lighting-layout { grid-template-columns: minmax(0, 1.25fr) minmax(0, .75fr); }
    #lighting-room { margin-top: 6px; }
    #lighting-room-label { display: block; margin-top: 16px; }
    #lighting-message { margin: 12px 0; }
    .light-rows { width: 100%; border-collapse: collapse; margin-top: 16px; }
    .light-rows td, .light-rows th { text-align: left; padding: 9px 5px;
      border-bottom: 1px solid var(--bociek-border); overflow-wrap: anywhere; }
    .light-rows .toggle-cell { text-align: center; width: 23%; }
    .light-rows input { width: 20px; min-height: 20px; margin: 6px; accent-color: #238d88; }
    .light-name { display: block; }
    .light-categories { display: flex; flex-wrap: wrap; align-items: center; gap: 2px 7px;
      margin: 6px 0 0; }
    .light-categories label { font-size: 13px; font-weight: 500; display: inline-flex;
      align-items: center; min-height: 32px; gap: 5px; }
    .light-rows .light-categories input { min-height: 16px; width: 16px; margin: 0; }
    #lighting-profiles { border: 1px solid var(--bociek-border); border-radius: 9px;
      padding: 10px 12px; }
    #lighting-profiles summary { font-weight: 650; cursor: pointer; padding: 4px 0; }
    .profile-content { display: grid; gap: 8px; margin-top: 8px; }
    .profile-targets { display: grid; gap: 2px; max-height: 15rem; overflow: auto; padding: 2px; }
    .profile-targets label, .profile-role-editor label {
      display: flex; gap: 8px; align-items: center; min-height: 34px;
      font-size: 14px; font-weight: 500; overflow-wrap: anywhere; }
    .profile-content input { width: 18px; min-height: 18px; flex: none; accent-color: #238d88; }
    .profile-role-editor { margin: 0; border: 1px solid var(--bociek-border);
      border-radius: 9px; padding: 8px 12px 12px; min-width: 0; }
    .profile-role-editor legend { font-size: 14px; font-weight: 650; }
    .profile-controls { display: flex; flex-wrap: wrap; gap: 8px; }
    #lighting-command-group-label { display: block; margin-top: 16px; }
    #lighting-command-group { margin: 6px 0 10px; }
    .plan-diff { display: grid; gap: 14px; margin-top: 16px; }
    .plan-diff section { border-left: 3px solid var(--primary-color, #238d88); padding: 2px 0 2px 10px; }
    .plan-diff h3 { font-size: 16px; margin-bottom: 6px; }
    .previous { margin: 0 0 6px; }
    [hidden] { display: none !important; }
    @media (max-width: 800px) { .layout { grid-template-columns: 1fr; } }
    @media (max-width: 500px) {
      main { padding: 16px 12px 30px; }
      .intro, .card { padding: 17px; }
      .intro img { width: 42px; height: 42px; }
    }
  </style>
  <div class="bar"><ha-menu-button></ha-menu-button><span>Bociek</span></div>
  <main>
    <header class="intro">
      <img alt="">
      <div><h1>Próba polecenia</h1><p>Sprawdź, jak Bociek zrozumie polecenie bez sterowania domem.</p></div>
    </header>
    <nav class="views" aria-label="Widoki Boćka">
      <button type="button" class="secondary" id="show-trial" aria-pressed="true">Próba polecenia</button>
      <button type="button" class="secondary" id="show-lighting" aria-pressed="false">Oświetlenie</button>
    </nav>
    <div class="layout" id="trial-view">
      <section class="card" aria-labelledby="input-title">
        <header><h2 id="input-title">Polecenie</h2></header>
        <form>
          <div class="field">
            <label for="room">Pokój, z którego mówisz</label>
            <select id="room"><option value="">Bez wskazywania pokoju satelity</option></select>
            <p class="muted">Jeśli w zdaniu wskażesz inny pokój, Bociek wybierze ten ze zdania.</p>
            <p id="rooms-error" class="muted" role="status" hidden>Nie udało się wczytać pokojów.</p>
            <button id="retry" type="button" hidden>Ponów wczytanie</button>
          </div>
          <div class="field">
            <label for="command">Co ma zrobić Bociek?</label>
            <input id="command" name="text" maxlength="400" required
              placeholder="np. Włącz światło w kuchni">
            <div class="examples" aria-label="Przykładowe polecenia">
              <button type="button" class="example">Włącz światło</button>
              <button type="button" class="example">Zgaś zapalone światła</button>
            </div>
          </div>
          <button type="submit">Sprawdź polecenie</button>
        </form>
      </section>
      <section class="card" aria-labelledby="result-title">
        <header><h2 id="result-title">Wynik próby</h2></header>
        <p id="message" class="muted" role="status">Wpisz polecenie, żeby zobaczyć wynik.</p>
        <div class="overview" hidden>
          <p id="status" role="status"></p>
          <section><h3>Pokój lub piętro</h3><p id="areas"></p></section>
          <section><h3>Wybrane urządzenia</h3><p id="targets"></p></section>
          <section id="details"><h3>Dlaczego taki wynik</h3><ul></ul></section>
          <section id="services"><h3>Planowane akcje</h3><p id="actions"></p></section>
        </div>
      </section>
    </div>
    <div class="layout lighting-layout" id="lighting-view" hidden>
      <section class="card" aria-labelledby="lighting-title">
        <header><h2 id="lighting-title">Światła w pokoju</h2>
          <p class="muted">Zestawy odpowiadają na ogólne polecenia. Kategorie pozwalają wskazać rodzaj światła.</p>
        </header>
        <label for="lighting-room" id="lighting-room-label">Pokój</label>
        <select id="lighting-room"><option value="">Wybierz pokój</option></select>
        <p class="muted" id="lighting-message" role="status">Wybierz pokój, żeby zobaczyć jego światła.</p>
        <details id="lighting-profiles" hidden>
          <summary>Zestawy ról dla pokoju</summary>
          <div class="profile-content">
            <label for="lighting-profile">Zestaw ról</label>
            <select id="lighting-profile"></select>
            <p class="muted" id="lighting-profile-roles" aria-live="polite"></p>
            <div class="profile-content" id="lighting-profile-apply">
              <div class="profile-targets" role="group" aria-label="Lampy do zestawu"></div>
              <p class="muted" id="lighting-profile-empty" hidden>Udostępnij lampy w Assist, aby zastosować zestaw.</p>
              <div class="profile-controls">
                <button type="button" class="secondary" id="lighting-profile-add" disabled>Dodaj do szkicu</button>
                <button type="button" class="secondary" id="lighting-profile-edit">Edytuj role zestawu</button>
              </div>
            </div>
            <fieldset class="profile-role-editor" hidden aria-describedby="lighting-profile-edit-help">
              <legend>Role zestawu</legend>
              <p class="muted" id="lighting-profile-edit-help">Zapis zestawu dotyczy jego przyszłego użycia. Role przypiszesz lampom osobno.</p>
              <div class="profile-role-inputs"></div>
              <div class="profile-controls">
                <button type="button" class="secondary" id="lighting-profile-reset">Użyj domyślnych ról</button>
                <button type="button" class="secondary" id="lighting-profile-cancel">Anuluj edycję</button>
                <button type="button" class="primary" id="lighting-profile-save" disabled>Zapisz zestaw</button>
              </div>
            </fieldset>
          </div>
        </details>
        <table class="light-rows" hidden>
          <thead><tr><th scope="col">Światło</th><th scope="col" class="toggle-cell">Włącz razem</th>
            <th scope="col" class="toggle-cell">Wyłącz razem</th></tr></thead>
          <tbody></tbody>
        </table>
        <div class="lighting-controls">
          <button type="button" class="secondary" id="lighting-refresh" disabled>Odśwież pokój</button>
        </div>
      </section>
      <section class="card" aria-labelledby="lighting-result">
        <header><h2 id="lighting-result">Reakcja na polecenia</h2></header>
        <label for="lighting-command-group" id="lighting-command-group-label">Rodzaj polecenia</label>
        <select id="lighting-command-group"><option value="">Polecenia ogólne</option></select>
        <div class="field" id="lighting-choice-test" hidden>
          <label for="lighting-choice-command">Polecenie do sprawdzenia</label>
          <input id="lighting-choice-command" maxlength="400" placeholder="${defaultChoiceCommand}"
            aria-describedby="lighting-choice-help">
          <p class="muted" id="lighting-choice-help">Wypróbuj „Włącz lampę”. Jedną preferowaną lampę Bociek wybierze od razu.</p>
        </div>
        <p class="muted" id="lighting-plan-help">Po wyborze pokoju zobaczysz bieżący plan.</p>
        <div class="plan-diff" id="lighting-plans"></div>
        <div class="lighting-controls">
          <button type="button" class="secondary" id="lighting-preview" disabled>Sprawdź zmiany</button>
          <button type="button" class="primary" id="lighting-save" disabled>Zapisz zmiany</button>
        </div>
      </section>
    </div>
    <a class="settings" href="/config/integrations/integration/polish_context_voice">
      Otwórz ustawienia integracji
    </a>
  </main>
`;

class BociekSettingsPanel extends HTMLElement {
  constructor() {
    super();
    this.view = this.attachShadow({ mode: "open" });
    this.view.append(template.content.cloneNode(true));
    this.$ = (query) => this.view.querySelector(query);
    this.$("form").addEventListener("submit", (event) => {
      event.preventDefault();
      void this.runTrial();
    });
    this.$("#retry").addEventListener("click", () => void this.loadRooms());
    this.draft = new Map();
    this.lightInputs = new Map();
    this.$("#show-trial").addEventListener("click", () => this.showLighting(false));
    this.$("#show-lighting").addEventListener("click", () => this.showLighting(true));
    this.$("#lighting-room").addEventListener("change", () => {
      const room = this.$("#lighting-room");
      if (this.draft.size || this.editingProfile) {
        room.value = this.lightingArea;
        this.tellLighting("Zapisz lub odrzuć zmiany przed zmianą pokoju.");
        return;
      }
      void this.loadLighting(room.value);
    });
    this.$("#lighting-refresh").addEventListener("click", () => void this.loadLighting(this.lightingArea));
    this.$("#lighting-preview").addEventListener("click", () => void this.reviewLighting(false));
    this.$("#lighting-save").addEventListener("click", () => void this.reviewLighting(true));
    this.$("#lighting-profile").addEventListener("change", () => this.describeProfile());
    this.$(".profile-targets").addEventListener("change", () => this.updateLightingActions());
    this.$("#lighting-profile-add").addEventListener("click", () => this.addProfileToDraft());
    this.$("#lighting-profile-edit").addEventListener("click", () => this.editProfile());
    this.$("#lighting-profile-cancel").addEventListener("click", () => this.closeProfileEditor());
    this.$("#lighting-profile-reset").addEventListener("click", () => this.resetProfile());
    this.$("#lighting-profile-save").addEventListener("click", () => void this.saveProfile());
    this.$(".profile-role-inputs").addEventListener("change", () => {
      this.profileReset = false;
      this.updateLightingActions();
    });
    const commandGroup = this.$("#lighting-command-group");
    for (const [key, caption] of lightingGroups.slice(1)) commandGroup.append(new Option(caption, key));
    commandGroup.addEventListener("change", () => {
      if (this.lightingPlans) this.renderLightingPlans(...this.lightingPlans);
      this.updateLightingActions();
    });
    this.$("#lighting-choice-command").addEventListener("input", () => {
      this.lightingReview = null;
      this.renderLightingPlans(this.lightingRoom.plans);
      this.tellLighting(this.draft.size ? "Sprawdź zmiany." : "Sprawdź polecenie.");
      this.updateLightingActions();
    });
    for (const button of this.view.querySelectorAll(".example")) {
      button.addEventListener("click", () => {
        const command = this.$("#command");
        command.value = button.textContent.trim();
        command.focus();
      });
    }
  }

  set panel(value) {
    const icon = value?.config?.icon;
    if (icon?.startsWith("/polish_context_voice/frontend/")) this.$("img").src = icon;
  }

  set hass(value) {
    this.api = value;
    this.$("ha-menu-button").hass = value;
    if (this.isConnected && !this.roomsRequested) void this.loadRooms();
  }

  set narrow(value) {
    this.$("ha-menu-button").narrow = value;
  }

  connectedCallback() {
    if (!this.roomsRequested && this.api) void this.loadRooms();
  }

  async loadRooms() {
    this.roomsRequested = true;
    const room = this.$("#room");
    const retry = this.$("#retry");
    const error = this.$("#rooms-error");
    retry.hidden = error.hidden = true;
    try {
      const result = await this.api.callWS({ type: trialType });
      const selected = room.value;
      while (room.options.length > 1) room.remove(1);
      for (const area of result.areas) room.append(new Option(area.name, area.id));
      const lighting = this.$("#lighting-room");
      const selectedLighting = lighting.value;
      while (lighting.options.length > 1) lighting.remove(1);
      for (const area of result.areas) lighting.append(new Option(area.name, area.id));
      if (result.areas.some((area) => area.id === selectedLighting)) {
        lighting.value = selectedLighting;
      }
      if (room.querySelector(`option[value="${CSS.escape(selected)}"]`)) room.value = selected;
    } catch {
      retry.hidden = error.hidden = false;
    }
  }

  showLighting(open) {
    this.$("#trial-view").hidden = open;
    this.$("#lighting-view").hidden = !open;
    this.$("#show-trial").setAttribute("aria-pressed", String(!open));
    this.$("#show-lighting").setAttribute("aria-pressed", String(open));
    this.$("h1").textContent = open ? "Oświetlenie" : "Próba polecenia";
    this.$(".intro p").textContent = open
      ? "Ustaw działanie poleceń głosowych w każdym pokoju."
      : "Sprawdź, jak Bociek zrozumie polecenie bez sterowania domem.";
  }

  tellLighting(message) { this.$("#lighting-message").textContent = message; }

  get editingProfile() { return !this.$(".profile-role-editor").hidden; }

  get choiceCommand() {
    return this.$("#lighting-choice-command").value.trim() || defaultChoiceCommand;
  }

  updateLightingActions() {
    const busy = Boolean(this.lightingBusy);
    const editing = this.editingProfile;
    this.$("#lighting-room").disabled = busy;
    const refresh = this.$("#lighting-refresh");
    refresh.disabled = busy || !this.lightingArea;
    refresh.textContent = this.draft.size || editing ? "Odrzuć szkic i odśwież" : "Odśwież pokój";
    const choice = this.$("#lighting-command-group").value === "preference";
    this.$("#lighting-choice-test").hidden = !choice || !this.lightingRoom;
    this.$("#lighting-choice-command").disabled = busy || editing;
    const checkCommand = choice && this.lightingRoom
      && this.choiceCommand !== this.lightingRoom.commands.preference;
    const preview = this.$("#lighting-preview");
    preview.disabled = busy || editing || !this.lightingRoom || (!this.draft.size && !checkCommand);
    preview.textContent = checkCommand && !this.draft.size ? "Sprawdź polecenie" : "Sprawdź zmiany";
    this.$("#lighting-save").disabled = busy || !this.lightingReview;
    this.$("#lighting-profile").disabled = busy || editing;
    this.$("#lighting-profile-add").disabled = busy || !this.$(".profile-targets input:checked");
    this.$("#lighting-profile-edit").disabled = busy;
    this.$(".profile-role-editor").disabled = busy;
    this.$("#lighting-profile-save").disabled = busy || !this.profileCanSave();
    for (const input of this.view.querySelectorAll(".profile-targets input")) input.disabled = busy;
    for (const input of this.view.querySelectorAll(".light-rows input")) {
      input.disabled = busy || editing || input.dataset.editable === "false";
    }
  }

  async loadLighting(area, savedMessage = "") {
    if (this.lightingBusy) return;
    const sameArea = area === this.lightingArea;
    const choiceCommand = sameArea ? this.choiceCommand : defaultChoiceCommand;
    const profileKey = sameArea ? this.$("#lighting-profile").value : "";
    const showProfiles = sameArea && this.$("#lighting-profiles").open;
    this.lightingArea = area;
    this.lightingRoom = this.lightingReview = null;
    this.lightingPlans = null;
    this.draft.clear();
    this.lightInputs.clear();
    this.$(".light-rows").hidden = true;
    this.$("#lighting-profiles").hidden = true;
    this.$("#lighting-profiles").open = false;
    this.$(".profile-role-editor").hidden = true;
    this.$("#lighting-profile-apply").hidden = false;
    this.$(".profile-targets").replaceChildren();
    this.$("#lighting-plans").replaceChildren();
    this.$("#lighting-plan-help").textContent = "Po wyborze pokoju zobaczysz bieżący plan.";
    this.lightingBusy = true;
    this.updateLightingActions();
    try {
      if (!area) {
        this.tellLighting("Wybierz pokój, żeby zobaczyć jego światła.");
        return;
      }
      this.tellLighting(savedMessage || "Wczytuję światła…");
      this.lightingRoom = await this.api.callWS({
        type: lightingType, area, choice_command: choiceCommand,
      });
      this.$("#lighting-choice-command").value = this.lightingRoom.commands.preference;
      this.renderLights(this.lightingRoom.lights);
      this.renderProfiles(profileKey);
      this.$("#lighting-profiles").open = showProfiles;
      this.renderLightingPlans(this.lightingRoom.plans);
      this.tellLighting(savedMessage || (this.lightingRoom.lights.length
        ? "Wybierz reakcję każdej lampy." : "W tym pokoju nie ma świateł przypisanych w HA."));
    } catch {
      this.tellLighting(savedMessage
        ? "Zapisano zmiany. Odśwież pokój, aby wczytać aktualne dane."
        : "Nie udało się wczytać świateł. Spróbuj odświeżyć pokój.");
    } finally {
      this.lightingBusy = false;
      this.updateLightingActions();
    }
  }

  renderProfiles(profileKey) {
    const profiles = this.lightingRoom.profiles || {};
    const lights = this.lightingRoom.lights.filter((light) => light.editable && light.exposed
      && !light.guards.some((role) => ["voice_block", "voice_exclude_all", "voice_high_risk"].includes(role)));
    const selector = this.$("#lighting-profile");
    selector.replaceChildren();
    for (const key of Object.keys(profileCategories)) {
      if (profiles[key]) selector.append(new Option(profileLabels.get(profileCategories[key]), key));
    }
    if (!selector.options.length) return;
    if (profiles[profileKey]) selector.value = profileKey;
    const targets = this.$(".profile-targets");
    for (const light of lights) {
      const label = document.createElement("label");
      const input = document.createElement("input");
      input.type = "checkbox";
      input.value = light.id;
      label.append(input, light.name);
      targets.append(label);
    }
    this.$("#lighting-profile-empty").hidden = lights.length > 0;
    this.$("#lighting-profile-add").hidden = lights.length === 0;
    this.$("#lighting-profile-edit").hidden = !this.lightingRoom.profile_revision;
    this.describeProfile();
    this.$("#lighting-profiles").hidden = false;
  }

  describeProfile() {
    const roles = this.lightingRoom.profiles[this.$("#lighting-profile").value];
    this.$("#lighting-profile-roles").textContent =
      "Dodaje role " + roles.map((role) => profileLabels.get(role)).join(", ") + ".";
  }

  editProfile() {
    if (this.draft.size) {
      this.tellLighting("Zapisz lub odrzuć szkic lamp przed edycją zestawu.");
      return;
    }
    const key = this.$("#lighting-profile").value;
    const inputs = this.$(".profile-role-inputs");
    inputs.replaceChildren();
    for (const [role, caption] of profileLabels) {
      const label = document.createElement("label");
      const input = document.createElement("input");
      input.type = "checkbox";
      input.value = role;
      input.checked = this.lightingRoom.profiles[key].includes(role);
      label.append(input, caption);
      inputs.append(label);
    }
    this.profileReset = false;
    this.$("#lighting-profile-apply").hidden = true;
    this.$(".profile-role-editor").hidden = false;
    this.updateLightingActions();
    inputs.querySelector("input").focus();
  }

  closeProfileEditor() {
    this.$(".profile-role-editor").hidden = true;
    this.$("#lighting-profile-apply").hidden = false;
    this.updateLightingActions();
    this.$("#lighting-profile-edit").focus();
  }

  resetProfile() {
    const defaults = this.lightingRoom.profile_defaults[this.$("#lighting-profile").value];
    for (const input of this.view.querySelectorAll(".profile-role-inputs input")) {
      input.checked = defaults.includes(input.value);
    }
    this.profileReset = true;
    this.updateLightingActions();
  }

  selectedProfileRoles() {
    return Array.from(this.view.querySelectorAll(".profile-role-inputs input:checked"),
      (input) => input.value);
  }

  profileCanSave() {
    if (!this.editingProfile) return false;
    const key = this.$("#lighting-profile").value;
    if (this.profileReset) return this.lightingRoom.custom_profiles.includes(key);
    const saved = this.lightingRoom.profiles[key];
    const selected = this.selectedProfileRoles();
    return selected.length > 0
      && (selected.length !== saved.length || selected.some((role) => !saved.includes(role)));
  }

  async saveProfile() {
    if (this.lightingBusy || !this.profileCanSave()) return;
    const area = this.lightingArea;
    const key = this.$("#lighting-profile").value;
    this.lightingBusy = true;
    this.updateLightingActions();
    try {
      await this.api.callWS({
        type: lightingType, area, action: "save_profile", revision: this.lightingRoom.profile_revision,
        profile: { key, roles: this.profileReset ? null : this.selectedProfileRoles() },
      });
    } catch (error) {
      this.tellLighting(lightingMessages[error?.code] || "Nie udało się zapisać zestawu.");
      return;
    } finally {
      this.lightingBusy = false;
      this.updateLightingActions();
    }
    await this.loadLighting(area, "Zapisano zestaw dla tego pokoju.");
    if (this.lightingRoom) this.$("#lighting-profile-edit").focus();
  }

  addProfileToDraft() {
    if (this.lightingBusy) return;
    const key = this.$("#lighting-profile").value;
    const roles = this.lightingRoom.profiles[key];
    const selected = [...this.view.querySelectorAll(".profile-targets input:checked")];
    if (!roles || !selected.length) return;
    const ids = new Set(selected.map((input) => input.value));
    for (const light of this.lightingRoom.lights) {
      if (!ids.has(light.id)) continue;
      const current = this.lightDraft(light);
      const change = {
        ...current,
        on: current.on || roles.includes("voice_default_on"),
        off: current.off || roles.includes("voice_all_off"),
        categories: lightCategories.map(([role]) => role)
          .filter((role) => current.categories.includes(role) || roles.includes(role)),
      };
      this.setLightDraft(light, change);
      const inputs = this.lightInputs.get(light.id);
      for (const [role, input] of inputs) {
        input.checked = role in change ? change[role] : change.categories.includes(role);
      }
    }
    for (const input of selected) input.checked = false;
    this.finishLightingEdit(profileCategories[key]);
  }

  renderLights(lights) {
    const table = this.$(".light-rows");
    table.tBodies[0].replaceChildren();
    table.hidden = lights.length === 0;
    for (const light of lights) {
      this.lightInputs.set(light.id, new Map());
      const row = table.tBodies[0].insertRow();
      const label = row.insertCell();
      const name = document.createElement("strong");
      name.className = "light-name";
      name.textContent = light.name;
      const meta = document.createElement("span");
      meta.className = "muted";
      meta.textContent = [
        !light.exposed && "Nieudostępnione w Assist",
        !light.available && "Niedostępne",
        !light.editable && "Brak wpisu w rejestrze encji",
        ...light.guards.map((role) => guardLabels[role]),
      ].filter(Boolean).join(" · ");
      label.append(name, meta);
      const categories = document.createElement("div");
      categories.className = "light-categories";
      for (const [key, caption] of lightCategories) {
        const toggle = document.createElement("label");
        toggle.append(this.lightToggle(light, key, caption, light.categories.includes(key)), caption);
        categories.append(toggle);
      }
      const preference = document.createElement("label");
      preference.append(this.lightToggle(light, "preferred", "Preferowana", light.preferred),
        "Preferowana");
      categories.append(preference);
      label.append(categories);
      for (const [key, operation] of [["on", "Włącz razem"], ["off", "Wyłącz razem"]]) {
        const cell = row.insertCell();
        cell.className = "toggle-cell";
        cell.append(this.lightToggle(light, key, operation, light[key]));
      }
    }
  }

  lightToggle(light, key, caption, checked) {
    const input = document.createElement("input");
    input.type = "checkbox";
    input.checked = checked;
    input.dataset.editable = String(light.editable);
    input.setAttribute("aria-label", caption + " — " + light.name);
    input.addEventListener("change", () => this.editLight(light, key, input.checked));
    this.lightInputs.get(light.id).set(key, input);
    return input;
  }

  lightDraft(light) {
    return {
      id: light.id, on: light.on, off: light.off, categories: light.categories,
      preferred: light.preferred,
      ...this.draft.get(light.id),
    };
  }

  editLight(light, key, selected) {
    const change = this.lightDraft(light);
    if (key === "on" || key === "off" || key === "preferred") change[key] = selected;
    else {
      change.categories = selected ? [...change.categories, key]
        : change.categories.filter((role) => role !== key);
    }
    this.setLightDraft(light, change);
    this.finishLightingEdit(key === "preferred" ? "preference" : key.startsWith("voice_") ? key : "");
  }

  setLightDraft(light, change) {
    const sameCategories = light.categories.length === change.categories.length
      && light.categories.every((role) => change.categories.includes(role));
    if (change.on === light.on && change.off === light.off && sameCategories
        && change.preferred === light.preferred) this.draft.delete(light.id);
    else this.draft.set(light.id, change);
  }

  finishLightingEdit(group) {
    this.$("#lighting-command-group").value = group;
    this.lightingReview = null;
    this.renderLightingPlans(this.lightingRoom.plans);
    this.tellLighting(this.draft.size ? "Sprawdź zmiany, żeby zobaczyć ich skutek." : "Brak zmian.");
    this.updateLightingActions();
  }

  renderLightingPlans(before, after, commands = this.lightingRoom.commands) {
    this.lightingPlans = [before, after, commands];
    const container = this.$("#lighting-plans");
    container.replaceChildren();
    const describe = (plan) => plan.targets.length && plan.actions.length
      ? plan.targets.join(", ") + (plan.status === "confirm" ? " · Wymaga potwierdzenia" : "")
      : plan.speech + (plan.preference_conflict ? " Kilka pasujących lamp ma tę samą preferencję." : "");
    const changed = after ? lightingGroups.filter(([group]) => planKeys(group).some((key) => {
      return describe(before[key]) !== describe(after[key]);
    })).map(([, label]) => label.toLocaleLowerCase("pl")) : [];
    this.$("#lighting-plan-help").textContent = after
      ? changed.length ? "Zmiany w podglądzie obejmują " + changed.join(", ") + "."
        : "Cele poleceń pozostaną takie same."
      : "Bieżące cele dla poleceń w tym pokoju.";
    const group = this.$("#lighting-command-group").value;
    for (const key of planKeys(group)) {
      const command = commands[key];
      const section = document.createElement("section");
      const title = document.createElement("h3");
      title.textContent = "„" + command + "”";
      const current = document.createElement("p");
      current.className = "previous muted";
      current.textContent = (after ? "Teraz: " : "") + describe(before[key]);
      section.append(title, current);
      if (after) {
        const proposed = document.createElement("p");
        const updated = describe(after[key]);
        proposed.textContent = updated === describe(before[key])
          ? "Po zapisie: bez zmian" : "Po zapisie: " + updated;
        section.append(proposed);
      }
      container.append(section);
    }
  }

  async reviewLighting(save) {
    if (this.lightingBusy || !this.lightingRoom) return;
    if (!this.draft.size) {
      if (!save && this.choiceCommand !== this.lightingRoom.commands.preference) {
        await this.loadLighting(this.lightingArea);
      }
      return;
    }
    const review = this.lightingReview;
    if (save && !review) return;
    this.lightingReview = null;
    this.lightingBusy = true;
    this.updateLightingActions();
    let savedMessage = "";
    try {
      const result = await this.api.callWS({
        type: lightingType, area: this.lightingArea, action: save ? "save" : "preview",
        revision: this.lightingRoom.revision, changes: [...this.draft.values()],
        choice_command: this.choiceCommand,
        ...(save ? { review } : {}),
      });
      if (result.error) {
        const detail = result.completed.length ? " Zapisano encje: " + result.completed.join(", ") : "";
        this.tellLighting((lightingMessages[result.error] || "Zapis został przerwany.") + detail);
      } else if (save) savedMessage = "Zapisano ustawienia świateł w tym pokoju.";
      else {
        this.lightingReview = result.review;
        this.renderLightingPlans(result.before, result.after, result.commands);
        this.tellLighting("Zmiany są gotowe do zapisania.");
      }
    } catch (error) {
      this.tellLighting(lightingMessages[error?.code] || "Nie udało się zakończyć operacji.");
    } finally {
      this.lightingBusy = false;
      this.updateLightingActions();
    }
    if (savedMessage) await this.loadLighting(this.lightingArea, savedMessage);
  }

  async runTrial() {
    const text = this.$("#command").value.trim();
    if (!text || !this.api || this.running) return;
    this.running = true;
    const submit = this.$("button[type=submit]");
    const area = this.$("#room").value;
    const message = this.$("#message");
    const overview = this.$(".overview");
    submit.disabled = true;
    message.hidden = overview.hidden = true;
    message.textContent = "Sprawdzam polecenie…";
    message.hidden = false;
    try {
      const result = await this.api.callWS({ type: trialType, text, ...(area ? { area } : {}) });
      this.$("#status").textContent = result.speech;
      for (const field of ["areas", "targets", "actions"]) {
        this.$("#" + field).textContent = result[field].join(", ") || "—";
      }
      this.$("#details").hidden = result.explanation.length === 0;
      this.$("#services").hidden = result.actions.length === 0;
      const details = result.explanation.map((line) => {
        const item = document.createElement("li");
        item.textContent = line;
        return item;
      });
      this.$("ul").replaceChildren(...details);
      message.hidden = true;
      overview.hidden = false;
    } catch (error) {
      message.textContent = error?.code === "invalid_area"
        ? "Ten pokój jest już niedostępny. Odśwież listę pokojów."
        : "Nie udało się sprawdzić polecenia. Spróbuj ponownie.";
      if (error?.code === "invalid_area") void this.loadRooms();
    } finally {
      this.running = false;
      submit.disabled = false;
    }
  }
}

if (!customElements.get("bociek-settings-panel")) {
  customElements.define("bociek-settings-panel", BociekSettingsPanel);
}
