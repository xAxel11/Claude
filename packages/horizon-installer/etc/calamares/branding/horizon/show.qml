// Slideshow shown while @DISTRO_NAME@ installs.
import QtQuick 2.0;
import calamares.slideshow 1.0;

Presentation
{
    id: presentation

    Timer {
        interval: 9000
        running: true
        repeat: true
        onTriggered: presentation.goToNextSlide()
    }

    component HSlide: Slide {
        property string heading
        property string body
        property string accent: "#8e5cf7"

        Rectangle {
            anchors.fill: parent
            radius: 18
            gradient: Gradient {
                GradientStop { position: 0.0; color: "#3a7bfd" }
                GradientStop { position: 0.6; color: accent }
                GradientStop { position: 1.0; color: "#f4589b" }
            }
        }
        Column {
            anchors.centerIn: parent
            width: parent.width * 0.75
            spacing: 18
            Text {
                width: parent.width
                text: heading
                color: "white"
                font.pixelSize: 38
                font.weight: Font.DemiBold
                horizontalAlignment: Text.AlignHCenter
                wrapMode: Text.WordWrap
            }
            Text {
                width: parent.width
                text: body
                color: "#f0f0ff"
                font.pixelSize: 19
                lineHeight: 1.3
                horizontalAlignment: Text.AlignHCenter
                wrapMode: Text.WordWrap
            }
        }
    }

    HSlide {
        heading: "Welcome to @DISTRO_NAME@"
        body: "A clean, elegant desktop built on Ubuntu LTS and KDE Plasma. Sit back — installation only takes a few minutes."
    }
    HSlide {
        heading: "Feels familiar"
        body: "A menu bar on top, a dock at the bottom, Launchpad for all your apps and Alt+Space to search everything."
        accent: "#5b6cff"
    }
    HSlide {
        heading: "Make it yours"
        body: "Open Horizon Settings to switch between light and dark, pick an accent colour, move the dock or change the whole layout."
        accent: "#b36bff"
    }
    HSlide {
        heading: "Smooth by design"
        body: "Genie minimise, window overview, blur and fluid animations — tuned for speed and fully adjustable."
        accent: "#ff6f9f"
    }
    HSlide {
        heading: "Thousands of apps"
        body: "Discover brings you the Ubuntu archive plus Flathub, with no Snap required."
        accent: "#6aa7ff"
    }
    HSlide {
        heading: "Private and secure"
        body: "No telemetry, full-disk encryption when you want it, and security updates from Ubuntu for years to come."
        accent: "#7c5cff"
    }
}
