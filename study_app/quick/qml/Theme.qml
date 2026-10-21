pragma Singleton
import QtQuick

QtObject {
    // One shared clock for decoration; the main window pauses it offscreen.
    property bool windEnabled: true
    property bool windActive: false
    property real windPhase: 0
    // Fast pressure rise, long release, and quiet gaps between unequal gusts.
    function gust(t, start, rise, fall, strength) {
        const age = t - start
        if (age <= 0 || age >= rise + fall) return 0
        if (age < rise) {
            const u = age / rise
            return strength * u * u * (3 - 2 * u)
        }
        const u = (age - rise) / fall
        return strength * (1 - u) * (1 - u)
    }
    function pressure(t) {
        return gust(t, 0.02, 0.016, 0.088, 1)
             + gust(t, 0.255, 0.026, 0.13, 0.68)
             + gust(t, 0.665, 0.018, 0.105, 1.3)
    }
    // Integrating pressure changes travel speed without jumping positions.
    function gustDistance(t, start, rise, fall, strength) {
        const age = t - start
        if (age <= 0) return 0
        const u = Math.min(1, age / rise)
        let area = rise * (u*u*u - 0.5*u*u*u*u)
        if (age > rise) {
            const v = Math.min(1, (age - rise) / fall)
            area += fall * (v - v*v + v*v*v / 3)
        }
        return area * strength
    }
    readonly property real windForce: windEnabled ? pressure(windPhase) : 0
    readonly property real windSway: windEnabled ? pressure(windPhase - 0.005)
                           * (1 + 0.045 * Math.sin(windPhase * Math.PI * 160)) : 0
    readonly property real bambooSway: windEnabled ? pressure(windPhase - 0.010)
                           * (1 + 0.075 * Math.sin(windPhase * Math.PI * 190 + 1.2)) : 0
    readonly property real windTravel: (gustDistance(windPhase, 0.02, 0.016, 0.088, 1)
                              + gustDistance(windPhase, 0.255, 0.026, 0.13, 0.68)
                              + gustDistance(windPhase, 0.665, 0.018, 0.105, 1.3))
                              / ((0.016/2 + 0.088/3) + 0.68*(0.026/2 + 0.13/3)
                                 + 1.3*(0.018/2 + 0.105/3))
    readonly property real paperLift: windEnabled
                                     ? Math.min(1, Math.max(0, pressure(windPhase - 0.013) - 0.15) / 0.85) : 0
    // Warm kozo paper, indigo ink, and restrained sakura pinks. Keep the
    // existing names stable because pages and bridges share these tokens.
    readonly property color background: "#F5EFE8"
    readonly property color paper: "#FFFBF5"
    readonly property color card: "#FFFDF8"
    readonly property color sidebar: "#F0E5E1"
    readonly property color peach: "#F5E9DD"
    readonly property color pink: "#E8AAC2"
    readonly property color pinkSoft: "#F5E4E4"
    readonly property color lavender: "#F1E7DE"
    readonly property color ink: "#393238"
    readonly property color muted: "#786E68"
    readonly property color accent: "#A65D72"
    readonly property color accentHover: "#934D63"
    readonly property color accentPressed: "#7D4054"
    readonly property color accentSoft: "#EED6DA"
    readonly property color focusRing: "#785766"
    readonly property color leaf: "#79816B"
    readonly property color line: "#E6DBD4"
    readonly property color warning: "#A74C4E"
    readonly property int sidebarExpandedWidth: 220
    readonly property int sidebarCollapsedWidth: 72
    readonly property int cardRadius: 5
    readonly property int controlRadius: 8
    readonly property int pageMargin: 20
    readonly property int panelSpacing: 20
    readonly property string fontFamily: "Noto Serif SC"
    readonly property string headingFontFamily: fontFamily
    readonly property int captionFontSize: 14
    readonly property int bodyFontSize: 18
    readonly property int sectionFontSize: 24
    readonly property int pageTitleSize: 48
}
