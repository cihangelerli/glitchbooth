// Global player to prevent memory leaks/multiple instances on mobile
let ambientPlayer = new Audio();
ambientPlayer.volume = 0.4;


const ambientTracks = [
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
    const track = ambientTracks[Math.floor(Math.random() * ambientTracks.length)];
    ambientPlayer.src = track;
    
    ambientPlayer.play().catch(err => {
        console.log("Playback failed, re-priming on next click.");
    });
}

// Loop logic
ambientPlayer.onended = () => {
    const nextDelay = 500 + Math.random() * 3000;
    setTimeout(playNextTrack, nextDelay);
};

// THE TRIGGER: Wait for the user to touch the screen
window.addEventListener('touchstart', function() {
    if (ambientPlayer.paused) {
        playNextTrack();
    }
}, { once: true });

window.addEventListener('click', function() {
    if (ambientPlayer.paused) {
        playNextTrack();
    }
}, { once: true });
