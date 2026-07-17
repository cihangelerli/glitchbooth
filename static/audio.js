// Global player to prevent memory leaks/multiple instances on legacy mobile engines
var ambientPlayer = new Audio();
ambientPlayer.volume = 0.4;

var ambientTracks = [
    "/static/sounds/glitch-01.wav",
    "/static/sounds/glitch-02.wav",
    "/static/sounds/glitch-03.wav",
    "/static/sounds/glitch-04.wav",
    "/static/sounds/glitch-05.wav",
    "/static/sounds/glitch-06.wav",
    "/static/sounds/glitch-07.wav",
    "/static/sounds/glitch-08.wav",
    "/static/sounds/glitch-09.wav",
    "/static/sounds/glitch-10.wav",
    "/static/sounds/glitch-11.wav",
    "/static/sounds/glitch-12.wav",
    "/static/sounds/glitch-13.wav"
];

function playNextTrack() {
    var randomIndex = Math.floor(Math.random() * ambientTracks.length);
    var track = ambientTracks[randomIndex];
    ambientPlayer.src = track;
    
    // Legacy Safety Wrap: Handles both modern promises and old synchronous engine rejections
    try {
        var playPromise = ambientPlayer.play();
        if (playPromise !== undefined && typeof playPromise.catch === 'function') {
            playPromise.catch(function(err) {
                console.log("Playback engine promise rejected:", err);
            });
        }
    } catch (err) {
        console.log("Playback engine synchronous block:", err);
    }
}

// Classical loop logic binding
ambientPlayer.onended = function() {
    var nextDelay = 500 + Math.random() * 3000;
    setTimeout(playNextTrack, nextDelay);
};

// User interaction gesture unlock handler tailored for old iOS WebKit variants
window.addEventListener('touchstart', function() {
    try {
        playNextTrack();
    } catch(e) {
        console.log("Interaction unlock deferred:", e);
    }
}, false);
