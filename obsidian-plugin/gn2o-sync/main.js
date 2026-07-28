const { Plugin, PluginSettingTab, Setting, Notice } = require("obsidian");
const { execFile } = require("child_process");

const DEFAULT_SETTINGS = { projectPath: "" };

class GN2OSettingsTab extends PluginSettingTab {
  constructor(app, plugin) { super(app, plugin); this.plugin = plugin; }
  display() {
    const { containerEl } = this;
    containerEl.empty();
    new Setting(containerEl).setName("GN2O project path").setDesc("Absolute path to the GN2O project folder.").addText(text => text
      .setPlaceholder("/Users/you/Documents/Projects/GN2O")
      .setValue(this.plugin.settings.projectPath)
      .onChange(async value => { this.plugin.settings.projectPath = value.trim(); await this.plugin.saveSettings(); }));
  }
}

module.exports = class GN2OSyncPlugin extends Plugin {
  async onload() {
    await this.loadSettings();
    this.addSettingTab(new GN2OSettingsTab(this.app, this));
    this.addCommand({ id: "generate-anki-proposals", name: "Generate Anki proposals", callback: () => this.run("--generate-anki-proposals") });
    this.addCommand({ id: "preview-approved-cards", name: "Preview approved Anki cards", callback: () => this.run("--sync-approved-cards") });
    this.addCommand({ id: "sync-approved-cards", name: "Sync approved Anki cards", callback: () => this.run("--sync-approved-cards", "--confirm") });
    this.addRibbonIcon("layers", "Sync approved GN2O Anki cards", () => {
      if (confirm("Create all approved GN2O Cloze cards in Anki?")) this.run("--sync-approved-cards", "--confirm");
    });
  }
  async loadSettings() { this.settings = Object.assign({}, DEFAULT_SETTINGS, await this.loadData()); }
  async saveSettings() { await this.saveData(this.settings); }
  subject() {
    const file = this.app.workspace.getActiveFile();
    if (!file) return "";
    return file.basename === "Hub" ? file.parent?.path.split("/").pop() : file.basename;
  }
  run(...args) {
    if (!this.settings.projectPath) return new Notice("GN2O project path is not configured.");
    const subject = this.subject();
    if (!subject) return new Notice("Open a GN2O subject note first.");
    const python = process.platform === "win32" ? "python" : "python3";
    execFile(python, ["main.py", ...args, "--subject", subject], { cwd: this.settings.projectPath }, (error, stdout, stderr) => {
      if (error) return new Notice(`GN2O failed: ${stderr || error.message}`, 10000);
      new Notice(stdout || "GN2O completed.", 8000);
    });
  }
};
