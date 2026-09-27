// Horizon OS layout: "macOS"
// Top menu bar (logo menu, global app menu, tray, clock) + floating centered dock.
// Runs as a Plasma layout script — both on first login (via the look-and-feel
// package) and when switching layouts in Horizon Settings.

var topBar = new Panel;
topBar.location = "top";
topBar.height = 2 * Math.round(gridUnit * 0.75);
topBar.floating = false;
topBar.hiding = "none";

var logoMenu = topBar.addWidget("org.kde.plasma.kickoff");
logoMenu.currentConfigGroup = ["General"];
logoMenu.writeConfig("icon", "horizon-logo-symbolic");
logoMenu.writeConfig("alphaSort", true);
logoMenu.writeConfig("favoritesPortedToKAstats", true);

topBar.addWidget("org.kde.plasma.appmenu");
topBar.addWidget("org.kde.plasma.panelspacer");
topBar.addWidget("org.kde.plasma.systemtray");

var clock = topBar.addWidget("org.kde.plasma.digitalclock");
clock.currentConfigGroup = ["Appearance"];
clock.writeConfig("showDate", true);
clock.writeConfig("dateDisplayFormat", "BesideTime");
clock.writeConfig("dateFormat", "custom");
clock.writeConfig("customDateFormat", "ddd d MMM");

// ---------------------------------------------------------------- dock ----
var dock = new Panel;
dock.location = "bottom";
dock.height = 2 * Math.round(gridUnit * 1.6);
dock.alignment = "center";
dock.lengthMode = "fit";
dock.floating = true;
dock.hiding = "dodgewindows";

var launchpad = dock.addWidget("org.kde.plasma.kickerdash");
launchpad.currentConfigGroup = ["General"];
launchpad.writeConfig("icon", "horizon-launchpad");

var tasks = dock.addWidget("org.kde.plasma.icontasks");
tasks.currentConfigGroup = ["General"];
tasks.writeConfig("launchers", [
    "applications:org.kde.dolphin.desktop",
    "applications:firefox.desktop",
    "applications:org.kde.konsole.desktop",
    "applications:org.kde.kate.desktop",
    "applications:org.kde.discover.desktop",
    "applications:org.horizon.settings.desktop",
    "applications:systemsettings.desktop"
]);
tasks.writeConfig("showOnlyCurrentDesktop", false);
tasks.writeConfig("groupingStrategy", 1);
tasks.writeConfig("indicateAudioStreams", true);
tasks.writeConfig("iconSpacing", 1);

dock.addWidget("org.kde.plasma.marginsseparator");
dock.addWidget("org.kde.plasma.trash");
