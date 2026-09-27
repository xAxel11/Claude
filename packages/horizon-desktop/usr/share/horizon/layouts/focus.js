// Horizon OS layout: "Focus"
// Slim top bar with a centered clock and an auto-hiding dock — maximum screen space.

var topBar = new Panel;
topBar.location = "top";
topBar.height = 2 * Math.round(gridUnit * 0.75);
topBar.floating = false;

var menu = topBar.addWidget("org.kde.plasma.kickoff");
menu.currentConfigGroup = ["General"];
menu.writeConfig("icon", "horizon-logo-symbolic");
topBar.addWidget("org.kde.plasma.appmenu");
topBar.addWidget("org.kde.plasma.panelspacer");
var clock = topBar.addWidget("org.kde.plasma.digitalclock");
clock.currentConfigGroup = ["Appearance"];
clock.writeConfig("showDate", true);
clock.writeConfig("dateDisplayFormat", "BesideTime");
topBar.addWidget("org.kde.plasma.panelspacer");
topBar.addWidget("org.kde.plasma.systemtray");

var dock = new Panel;
dock.location = "bottom";
dock.height = 2 * Math.round(gridUnit * 1.5);
dock.alignment = "center";
dock.lengthMode = "fit";
dock.floating = true;
dock.hiding = "autohide";

var launchpad = dock.addWidget("org.kde.plasma.kickerdash");
launchpad.currentConfigGroup = ["General"];
launchpad.writeConfig("icon", "horizon-launchpad");
var tasks = dock.addWidget("org.kde.plasma.icontasks");
tasks.currentConfigGroup = ["General"];
tasks.writeConfig("launchers", [
    "applications:org.kde.dolphin.desktop",
    "applications:firefox.desktop",
    "applications:org.kde.konsole.desktop",
    "applications:org.horizon.settings.desktop"
]);
