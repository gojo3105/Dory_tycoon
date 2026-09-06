using System.Collections;
using GameFactory.Core;
using GameFactory.Modules.GravitySwitch;
using UnityEngine;

namespace GameFactory.Gameplay.Runner
{
    /// <summary>
    /// Auto-runs forward. Tap to jump (twice, when the spec allows it), swipe
    /// down to slide under overhead obstacles. Dies on Obstacle contact.
    ///
    /// TWO VERBS, NOT ONE. Tap-to-jump alone asks the player the same question
    /// at every obstacle - "press now?" - so the level can only vary its
    /// rhythm, never its content. The endless runners this genre is measured
    /// against are built on a pair: something to go over and something to go
    /// under, with a double jump for the gaps a single one cannot reach. Each
    /// verb is switched on by its own GameSpec mechanics flag, so a spec that
    /// wants the plain one-button game still generates exactly that.
    /// </summary>
    [RequireComponent(typeof(Rigidbody2D))]
    [RequireComponent(typeof(Collider2D))]
    public class RunnerPlayerController : MonoBehaviour
    {
        [SerializeField] private float moveSpeed = 6f;
        [SerializeField] private float jumpPower = 10f;
        [Tooltip("Multiplier on Unity's gravity. See GameSpec player.gravityScale.")]
        [SerializeField] private float gravityScale = 3.5f;
        [SerializeField] private bool gravitySwitchEnabled;
        [SerializeField] private LayerMask groundLayer;
        [SerializeField] private Transform groundCheck;
        [SerializeField] private float groundCheckRadius = 0.15f;

        [Header("Double jump")]
        [Tooltip("Allows one extra jump in mid-air. See GameSpec mechanics.doubleJump.")]
        [SerializeField] private bool doubleJumpEnabled;
        [Tooltip("Fraction of jumpPower the second, mid-air jump uses.")]
        [SerializeField] private float doubleJumpFraction = 0.85f;

        [Header("Slide")]
        [Tooltip("Allows ducking under overhead obstacles. See GameSpec mechanics.slide.")]
        [SerializeField] private bool slideEnabled;
        [Tooltip("Collider height while sliding, as a fraction of standing height.")]
        [SerializeField] private float slideHeightFraction = 0.5f;
        [Tooltip("Longest one slide may last, in seconds, however long the input is held.")]
        [SerializeField] private float maxSlideSeconds = 1.5f;
        [Tooltip("Downward speed applied when the slide input arrives in mid-air.")]
        [SerializeField] private float diveSpeed = 16f;

        [SerializeField] private float hitStopDuration = 0.05f;
        [SerializeField] private float hitStopTimeScale = 0.05f;
        [SerializeField] private float hitShakeDuration = 0.2f;
        [SerializeField] private float hitShakeMagnitude = 0.15f;

        private Rigidbody2D body;
        private BoxCollider2D box;
        private CameraFollow2D cameraFollow;
        private bool isGrounded;
        private bool isDead;

        // Slide state. standingSize/standingOffset are the collider as the
        // prefab generator built it, so a slide can always put it back exactly
        // rather than recomputing a "standing" box from the shrunken one.
        private Vector2 standingSize;
        private Vector2 standingOffset;
        private bool isSliding;
        private float slideTimer;
        private bool slideHeld;

        // Jumps spent since the last landing, so a double jump is two and not
        // an unlimited stream of taps.
        private int jumpsUsed;

        private static AudioClip jumpClip;
        private static AudioClip slideClip;

        /// <summary>Read by the visual-only RunnerCharacterMotion. Physics stays private.</summary>
        public bool IsGrounded => isGrounded;

        public bool IsDead => isDead;

        /// <summary>True while ducking. Read by RunnerCharacterMotion to match the pose to the hitbox.</summary>
        public bool IsSliding => isSliding;

        /// <summary>
        /// How short the collider gets while sliding. The visual reads this
        /// rather than keeping its own number: a character drawn at full height
        /// over a half-height hitbox looks like it clipped through the bar.
        /// </summary>
        public float SlideHeightFraction => slideHeightFraction;

        /// <summary>Vertical speed, for squash and stretch. Zero before Awake.</summary>
        public float VerticalVelocity => body != null ? body.linearVelocity.y : 0f;

        /// <summary>Forward speed, for pacing the run cycle. Zero before Awake, and on the title screen.</summary>
        public float HorizontalVelocity => body != null ? body.linearVelocity.x : 0f;

        /// <summary>Applies GameSpec-driven tuning. Called at runtime by RunnerGameInitializer.</summary>
        public void Configure(float speed, float jump, bool useGravitySwitch,
                              float gravity = 3.5f, bool useDoubleJump = false,
                              bool useSlide = false)
        {
            moveSpeed = speed;
            jumpPower = jump;
            gravitySwitchEnabled = useGravitySwitch;
            gravityScale = Mathf.Max(0.1f, gravity);
            doubleJumpEnabled = useDoubleJump;
            slideEnabled = useSlide;
            if (body != null) body.gravityScale = gravityScale;
            if (!slideEnabled) EndSlide();
        }

        /// <summary>Forward distance a jump covers, in world units.

        /// The level generator needs this: spacing obstacles without knowing
        /// how far a jump actually reaches is what made them unclearable.
        /// </summary>
        public static float JumpDistance(float speed, float jump, float gravity)
        {
            float g = Mathf.Abs(Physics2D.gravity.y) * Mathf.Max(0.1f, gravity);
            return speed * (2f * jump / g);
        }

        /// <summary>Wires structural references. Called at edit time by SceneGenerator.</summary>
        public void SetGroundCheck(Transform check, LayerMask layer)
        {
            groundCheck = check;
            groundLayer = layer;
        }

        private void Awake()
        {
            body = GetComponent<Rigidbody2D>();
            box = GetComponent<BoxCollider2D>();
            if (box != null)
            {
                standingSize = box.size;
                standingOffset = box.offset;
            }

            // Applied here as well as in Configure: Awake runs before the
            // initializer reads the spec, and a single frame at gravity 1 is
            // enough to visibly float the character on the title screen.
            body.gravityScale = gravityScale;
            cameraFollow = Camera.main != null ? Camera.main.GetComponent<CameraFollow2D>() : null;
            GravitySwitchController.ResetToDefault();
        }

        private void OnEnable()
        {
            TapInput.Tapped += HandleTap;
            TapInput.SwipedDown += HandleSwipeDown;
            TapInput.SwipeReleased += HandleSwipeReleased;
        }

        private void OnDisable()
        {
            TapInput.Tapped -= HandleTap;
            TapInput.SwipedDown -= HandleSwipeDown;
            TapInput.SwipeReleased -= HandleSwipeReleased;
        }

        private void FixedUpdate()
        {
            if (groundCheck != null)
            {
                isGrounded = Physics2D.OverlapCircle(groundCheck.position, groundCheckRadius, groundLayer);
            }

            // Refunding the jumps needs BOTH the ground check and a body that
            // is no longer travelling upward. The check circle has a radius of
            // 0.15 and one physics step at jump speed clears barely more than
            // that, so "grounded" alone is still true for a frame or two after
            // take-off - long enough for a fast tapper to jump forever.
            if (isGrounded && UpwardVelocity <= 0.01f) jumpsUsed = 0;

            UpdateSlide();

            // Ground check above still runs on the title screen so the
            // character visibly stands on the start line instead of hovering;
            // only forward movement itself waits for Play.
            if (isDead || GameManager.Instance == null || GameManager.Instance.CurrentState != GameManager.GameState.Playing) return;

            body.linearVelocity = new Vector2(moveSpeed, body.linearVelocity.y);
        }

        /// <summary>
        /// Vertical speed measured against whichever way is currently "up".
        /// Under an inverted gravity zone a jump reads as a NEGATIVE y speed,
        /// so comparing raw velocity.y would refund the jump instantly.
        /// </summary>
        private float UpwardVelocity
        {
            get
            {
                if (body == null) return 0f;
                bool inverted = gravitySwitchEnabled && GravitySwitchController.IsInverted;
                return inverted ? -body.linearVelocity.y : body.linearVelocity.y;
            }
        }

        private void HandleTap()
        {
            if (isDead || !CanAct) return;

            // Running off a ledge without jumping spends the ground jump: the
            // air jump is a recovery, not a free extra one.
            if (!isGrounded && jumpsUsed == 0) jumpsUsed = 1;

            int allowedJumps = doubleJumpEnabled ? 2 : 1;
            if (jumpsUsed >= allowedJumps) return;

            bool inverted = gravitySwitchEnabled && GravitySwitchController.IsInverted;
            float jumpDirection = inverted ? -1f : 1f;
            // The second jump is deliberately weaker. At full power it doubles
            // the arc height and the player leaves the top of the screen.
            float power = jumpsUsed == 0 ? jumpPower : jumpPower * doubleJumpFraction;
            jumpsUsed++;

            EndSlide();
            body.linearVelocity = new Vector2(body.linearVelocity.x, jumpDirection * power);

            if (jumpClip == null) jumpClip = ProceduralTone.Sine("SFX_Jump", 620f, 0.12f);
            AudioManager.Instance?.PlaySfx(jumpClip);
            if (SettingsSystem.VibrationEnabled) Handheld.Vibrate();
        }

        // ---- slide -----------------------------------------------------------

        private bool CanAct =>
            GameManager.Instance != null
            && GameManager.Instance.CurrentState == GameManager.GameState.Playing;

        private void HandleSwipeDown()
        {
            if (isDead || !slideEnabled || !CanAct) return;

            slideHeld = true;

            if (isGrounded)
            {
                BeginSlide();
                return;
            }

            // Swiped in mid-air: dive. Landing early is the point - an overhead
            // bar arrives while the player is still coming down from a jump,
            // and waiting out the arc means hitting it standing up. The slide
            // itself starts on touchdown, from UpdateSlide.
            bool inverted = gravitySwitchEnabled && GravitySwitchController.IsInverted;
            body.linearVelocity = new Vector2(body.linearVelocity.x,
                                              inverted ? diveSpeed : -diveSpeed);
        }

        private void HandleSwipeReleased()
        {
            slideHeld = false;
            EndSlide();
        }

        private void UpdateSlide()
        {
            if (isDead)
            {
                EndSlide();
                return;
            }

            if (isSliding)
            {
                slideTimer += Time.fixedDeltaTime;
                // Standing back up when the ground runs out matters: a slide
                // carried off a ledge would otherwise land in a half-height
                // hitbox and walk on through the next ground-level obstacle.
                if (!isGrounded || !slideHeld || slideTimer >= maxSlideSeconds) EndSlide();
                return;
            }

            // Held through a dive: the duck begins the moment the feet land.
            if (slideHeld && slideEnabled && isGrounded && CanAct) BeginSlide();
        }

        private void BeginSlide()
        {
            if (isSliding || box == null) return;

            isSliding = true;
            slideTimer = 0f;

            // Halve the box and push it down by a quarter of the standing
            // height, which keeps the BOTTOM edge exactly where it was. The
            // feet stay planted on the ground and the ground check - a child
            // transform at the feet - keeps reporting correctly; only the head
            // comes down, which is the whole point.
            float slideHeight = standingSize.y * Mathf.Clamp(slideHeightFraction, 0.2f, 0.95f);
            box.size = new Vector2(standingSize.x, slideHeight);
            box.offset = new Vector2(standingOffset.x,
                                     standingOffset.y - (standingSize.y - slideHeight) * 0.5f);

            if (slideClip == null) slideClip = ProceduralTone.Sine("SFX_Slide", 240f, 0.16f);
            AudioManager.Instance?.PlaySfx(slideClip);
        }

        private void EndSlide()
        {
            if (!isSliding) return;

            isSliding = false;
            slideTimer = 0f;
            if (box == null) return;

            box.size = standingSize;
            box.offset = standingOffset;
        }

        private void OnTriggerEnter2D(Collider2D other)
        {
            if (other.CompareTag("Obstacle"))
            {
                Die();
            }
        }

        private void Die()
        {
            if (isDead) return;

            isDead = true;
            EndSlide();
            body.linearVelocity = Vector2.zero;
            cameraFollow?.Shake(hitShakeDuration, hitShakeMagnitude);
            StartCoroutine(HitStop(hitStopDuration, hitStopTimeScale));
            if (SettingsSystem.VibrationEnabled) Handheld.Vibrate();
            GameManager.Instance.TriggerGameOver();
        }

        /// <summary>Briefly slows time on impact, then restores it - purely a game-feel beat, not a state change.</summary>
        private IEnumerator HitStop(float duration, float slowTimeScale)
        {
            Time.timeScale = slowTimeScale;
            yield return new WaitForSecondsRealtime(duration);
            Time.timeScale = 1f;
        }
    }
}
