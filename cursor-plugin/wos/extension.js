"use strict";

const vscode = require("vscode");

const REFERENCE_PNG_RE = /\/references\/.+\.png$/i;
const MODULE_FROM_REF_RE = /^modules\/(.+?)\/references\//;
const SCENARIO_YAML_RE = /\/scenarios\/.+\.ya?ml$/i;
const MODULE_FROM_SCENARIO_RE = /^modules\/(.+?)\/scenarios\//;
const TEMPLATE_STEM_RE = /\{[^}]+\}/;

/**
 * @returns {vscode.WorkspaceConfiguration}
 */
function uiConfig() {
  return vscode.workspace.getConfiguration("wos.labeling");
}

/**
 * @param {vscode.WorkspaceConfiguration} config
 * @returns {{ host: string; port: number }}
 */
function streamlitEndpoint(config) {
  const host = String(config.get("host", "127.0.0.1")).trim() || "127.0.0.1";
  const port = Number(config.get("port", 8501)) || 8501;
  return { host, port };
}

/**
 * @param {string} page
 * @param {Record<string, string>} query
 * @param {vscode.WorkspaceConfiguration} config
 * @returns {string}
 */
function buildStreamlitUrl(page, query, config) {
  const { host, port } = streamlitEndpoint(config);
  const params = new URLSearchParams(query);
  const qs = params.toString();
  return `http://${host}:${port}/${page}${qs ? `?${qs}` : ""}`;
}

/**
 * @param {vscode.Uri | undefined} uri
 * @returns {boolean}
 */
function isReferencePng(uri) {
  if (!uri || uri.scheme !== "file") {
    return false;
  }
  return REFERENCE_PNG_RE.test(uri.fsPath.replace(/\\/g, "/"));
}

/**
 * @param {vscode.Uri | undefined} uri
 * @returns {boolean}
 */
function isScenarioYaml(uri) {
  if (!uri || uri.scheme !== "file") {
    return false;
  }
  const path = uri.fsPath.replace(/\\/g, "/");
  if (/\/drafts\//i.test(path)) {
    return false;
  }
  return SCENARIO_YAML_RE.test(path);
}

/**
 * @returns {vscode.Uri | undefined}
 */
function getActiveResourceUri() {
  const tab = vscode.window.tabGroups.activeTabGroup.activeTab;
  if (tab?.input instanceof vscode.TabInputText) {
    return tab.input.uri;
  }
  if (tab?.input instanceof vscode.TabInputCustom) {
    return tab.input.uri;
  }
  const editor = vscode.window.activeTextEditor;
  return editor?.document.uri;
}

/**
 * @param {string} workspaceRoot
 * @param {string} filePath
 * @returns {string | undefined}
 */
function repoRelativePath(workspaceRoot, filePath) {
  const root = workspaceRoot.replace(/\\/g, "/").replace(/\/$/, "");
  const file = filePath.replace(/\\/g, "/");
  if (!file.startsWith(root + "/") && file !== root) {
    return undefined;
  }
  return file.slice(root.length).replace(/^\//, "");
}

/**
 * @param {string} relPath
 * @returns {string}
 */
function resolveReferenceModuleKey(relPath) {
  if (relPath.startsWith("references/")) {
    return "core";
  }
  const match = relPath.match(MODULE_FROM_REF_RE);
  return match ? match[1] : "core";
}

/**
 * @param {string} relPath
 * @returns {string}
 */
function resolveScenarioModuleKey(relPath) {
  const match = relPath.match(MODULE_FROM_SCENARIO_RE);
  return match ? match[1] : "all";
}

/**
 * @param {string} relPath
 * @returns {string | undefined}
 */
function resolveScenarioKey(relPath) {
  const fileName = relPath.split("/").pop() || "";
  const stem = fileName.replace(/\.ya?ml$/i, "");
  if (!stem || TEMPLATE_STEM_RE.test(stem)) {
    return undefined;
  }
  return stem;
}

/**
 * @param {string} relPath
 * @param {vscode.WorkspaceConfiguration} config
 * @returns {string}
 */
function buildLabelingUrl(relPath, config) {
  return buildStreamlitUrl(
    "labeling",
    {
      ref: relPath,
      module: resolveReferenceModuleKey(relPath),
      version: "default",
    },
    config,
  );
}

/**
 * @param {string} relPath
 * @param {vscode.WorkspaceConfiguration} config
 * @returns {string}
 */
function buildScenarioRunUrl(relPath, config) {
  const query = { module: resolveScenarioModuleKey(relPath) };
  const scenarioKey = resolveScenarioKey(relPath);
  if (scenarioKey) {
    query.scenario = scenarioKey;
  }
  return buildStreamlitUrl("debug_scenarios", query, config);
}

/**
 * @param {vscode.Uri} uri
 * @returns {{ relPath: string; url: string } | undefined}
 */
function resolveLabelingTarget(uri) {
  if (!isReferencePng(uri)) {
    return undefined;
  }
  const folder = vscode.workspace.getWorkspaceFolder(uri);
  if (!folder) {
    return undefined;
  }
  const relPath = repoRelativePath(folder.uri.fsPath, uri.fsPath);
  if (!relPath) {
    return undefined;
  }
  const config = uiConfig();
  return { relPath, url: buildLabelingUrl(relPath, config) };
}

/**
 * @param {vscode.Uri} uri
 * @returns {{ relPath: string; url: string; scenarioKey?: string } | undefined}
 */
function resolveScenarioTarget(uri) {
  if (!isScenarioYaml(uri)) {
    return undefined;
  }
  const folder = vscode.workspace.getWorkspaceFolder(uri);
  if (!folder) {
    return undefined;
  }
  const relPath = repoRelativePath(folder.uri.fsPath, uri.fsPath);
  if (!relPath) {
    return undefined;
  }
  const config = uiConfig();
  const scenarioKey = resolveScenarioKey(relPath);
  return {
    relPath,
    scenarioKey,
    url: buildScenarioRunUrl(relPath, config),
  };
}

/**
 * @param {vscode.Uri | undefined} uri
 */
async function openInLabeling(uri) {
  const targetUri = uri || getActiveResourceUri();
  if (!targetUri) {
    vscode.window.showWarningMessage("No reference image is open.");
    return;
  }
  const target = resolveLabelingTarget(targetUri);
  if (!target) {
    vscode.window.showWarningMessage("Open a PNG under a references/ directory.");
    return;
  }
  await vscode.env.openExternal(vscode.Uri.parse(target.url));
}

/**
 * @param {vscode.Uri | undefined} uri
 */
async function copyLabelingLink(uri) {
  const targetUri = uri || getActiveResourceUri();
  if (!targetUri) {
    vscode.window.showWarningMessage("No reference image is open.");
    return;
  }
  const target = resolveLabelingTarget(targetUri);
  if (!target) {
    vscode.window.showWarningMessage("Open a PNG under a references/ directory.");
    return;
  }
  await vscode.env.clipboard.writeText(target.url);
  vscode.window.showInformationMessage("Labeling link copied.");
}

/**
 * @param {vscode.Uri | undefined} uri
 */
async function runScenario(uri) {
  const targetUri = uri || getActiveResourceUri();
  if (!targetUri) {
    vscode.window.showWarningMessage("No scenario file is open.");
    return;
  }
  const target = resolveScenarioTarget(targetUri);
  if (!target) {
    vscode.window.showWarningMessage("Open a YAML file under a scenarios/ directory.");
    return;
  }
  await vscode.env.openExternal(vscode.Uri.parse(target.url));
}

/**
 * @param {vscode.Uri | undefined} uri
 */
async function copyScenarioRunLink(uri) {
  const targetUri = uri || getActiveResourceUri();
  if (!targetUri) {
    vscode.window.showWarningMessage("No scenario file is open.");
    return;
  }
  const target = resolveScenarioTarget(targetUri);
  if (!target) {
    vscode.window.showWarningMessage("Open a YAML file under a scenarios/ directory.");
    return;
  }
  await vscode.env.clipboard.writeText(target.url);
  vscode.window.showInformationMessage("Scenario runner link copied.");
}

function updateUi() {
  const uri = getActiveResourceUri();
  const referenceActive = !!uri && isReferencePng(uri);
  const scenarioActive = !!uri && isScenarioYaml(uri);

  return { referenceActive, scenarioActive };
}

class WosScenarioCodeLensProvider {
  /**
   * @param {vscode.TextDocument} document
   * @returns {vscode.CodeLens[]}
   */
  provideCodeLenses(document) {
    if (!isScenarioYaml(document.uri)) {
      return [];
    }
    const target = resolveScenarioTarget(document.uri);
    if (!target) {
      return [];
    }
    const top = new vscode.Range(0, 0, 0, 0);
    return [
      new vscode.CodeLens(top, {
        title: "$(play) Run in WOS",
        command: "wos.runScenario",
        arguments: [document.uri],
      }),
      new vscode.CodeLens(top, {
        title: "$(link-external) Copy runner link",
        command: "wos.copyScenarioRunLink",
        arguments: [document.uri],
      }),
    ];
  }
}

/**
 * @param {import("vscode").ExtensionContext} context
 */
function activate(context) {
  const labelingStatus = vscode.window.createStatusBarItem(
    vscode.StatusBarAlignment.Right,
    101,
  );
  labelingStatus.command = "wos.openInLabeling";
  labelingStatus.tooltip = "Open this reference in WOS Labeling UI";

  const runStatus = vscode.window.createStatusBarItem(
    vscode.StatusBarAlignment.Right,
    100,
  );
  runStatus.command = "wos.runScenario";
  runStatus.tooltip = "Open this scenario in WOS Scenario runner";

  const refreshUi = () => {
    const { referenceActive, scenarioActive } = updateUi();
    if (scenarioActive) {
      runStatus.text = "$(play) Run";
      runStatus.show();
    } else {
      runStatus.hide();
    }
    if (referenceActive) {
      labelingStatus.text = "$(link-external) Labeling";
      labelingStatus.show();
    } else {
      labelingStatus.hide();
    }
  };

  const codeLensProvider = new WosScenarioCodeLensProvider();
  context.subscriptions.push(
    vscode.window.onDidChangeActiveTextEditor(() => refreshUi()),
    vscode.window.tabGroups.onDidChangeTabs(() => refreshUi()),
    vscode.workspace.onDidChangeWorkspaceFolders(() => refreshUi()),
    vscode.workspace.onDidOpenTextDocument(() => refreshUi()),
    vscode.workspace.onDidCloseTextDocument(() => refreshUi()),
    vscode.languages.registerCodeLensProvider(
      [{ language: "yaml", scheme: "file" }],
      codeLensProvider,
    ),
    labelingStatus,
    runStatus,
    vscode.commands.registerCommand("wos.openInLabeling", openInLabeling),
    vscode.commands.registerCommand("wos.copyLabelingLink", copyLabelingLink),
    vscode.commands.registerCommand("wos.runScenario", runScenario),
    vscode.commands.registerCommand("wos.copyScenarioRunLink", copyScenarioRunLink),
  );

  refreshUi();
}

function deactivate() {}

module.exports = { activate, deactivate };
