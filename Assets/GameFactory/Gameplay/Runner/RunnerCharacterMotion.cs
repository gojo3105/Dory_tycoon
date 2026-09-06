using UnityEngine;

namespace GameFactory.Gameplay.Runner
{
    /// <summary>
    /// Makes a single static sprite read as a character that is running,
    /// jumping and landing - without a single extra frame of art.
    ///
    /// WHY THIS EXISTS. 도리 is one cut-out PNG. There is no animator, no
    /// sprite sheet, no rig, so the character otherwise slides along the ground
    /// as a decal. Squash and stretch, a run bob and a lean carry most of what
    /// a run cycle communicates - weight, rhythm, and the moment of impact -
    /// and they cost nothing per frame. Real frames, when they exist, layer on
    /// top of this rather than replacing it: animators keep using squash and
    /// stretch precisely because frames alone do not sell weight.
    ///
    /// VISUAL ONLY. This lives on a child transform and scales and rotates that
    /// child. Putting it on the player root would scale the BoxCollider2D with
    /// it, so the hitbox would breathe in and out while running - which is the
    /// same class of bug as the collider/visual offset that made coin pickups
    /// feel wrong.
    /// </summary>
    [DisallowMultipleComponent]
    public class RunnerCharacterMotion : MonoBehaviour
    {
        // AMPLITUDES. These were four to five times smaller until 2026-09-05,
        // because the task that added them said "keep it subtle - limbs that
        // swing far read as broken". That was my note and it was wrong for the
        // screen this runs on: the player reported "the image just slides
        // sideways", meaning none of it registered at all. On a phone, at this
        // sprite size, subtle IS invisible. A runner has to read as running
        // from a glance at arm's length.
        [Header("Run")]
        [Tooltip("Bounces per second while grounded.")]
        [SerializeField] private float bobFrequency = 4.5f;
        [Tooltip("Vertical travel of the bob, in world units.")]
        [SerializeField] private float bobHeight = 0.16f;
        [Tooltip("How much the body squashes at the bottom of each bob, 0-1.")]
        [SerializeField] private float bobSquash = 0.12f;
        [Tooltip("Forward lean while running, in degrees.")]
        [SerializeField] private float runLean = 8f;

        [Header("Limbs")]
        [Tooltip("Maximum limb rotation during the grounded run cycle, in degrees.")]
        [SerializeField] private float limbSwing = 34f;
        [Tooltip("Arm angle held while airborne, in degrees.")]
        [SerializeField] private float jumpArmAngle = 40f;
        [Tooltip("Leg angle held while airborne, in degrees.")]
        [SerializeField] private float jumpLegAngle = 26f;
        [SerializeField] private Color limbColor = new Color(0.95f, 0.87f, 0.75f, 1f);

        [Header("Slide")]
        // The squash itself is NOT tuned here - it is read from the controller,
        // because the visible height and the hitbox height have to be the same
        // number. A character drawn standing over a ducked hitbox slips under
        // an overhead bar looking like it clipped straight through it.
        [Tooltip("Forward lean while sliding, in degrees.")]
        [SerializeField] private float slideLean = 26f;
        [Tooltip("How far the legs are thrown forward during a slide, in degrees.")]
        [SerializeField] private float slideLegAngle = 74f;
        [Tooltip("How far the arms tuck back during a slide, in degrees.")]
        [SerializeField] private float slideArmAngle = 46f;

        [Header("Air")]
        [Tooltip("Vertical speed that produces the full stretch or squash.")]
        [SerializeField] private float velocityForFullStretch = 12f;
        [Tooltip("Peak stretch while rising, 0-1.")]
        [SerializeField] private float airStretch = 0.16f;
        [Tooltip("Backward tilt at the top of a jump, in degrees.")]
        [SerializeField] private float airTilt = 9f;

        [Header("Landing")]
        [Tooltip("Squash applied the instant the feet touch down, 0-1.")]
        [SerializeField] private float landSquash = 0.28f;
        [Tooltip("Seconds for the landing squash to spring back.")]
        [SerializeField] private float landRecovery = 0.18f;

        [Header("Response")]
        [Tooltip("Seconds for scale to chase its target. Small is snappy.")]
        [SerializeField] private float scaleSmoothing = 0.045f;
        [SerializeField] private float rotationSmoothing = 0.08f;

        [Header("Rig")]
        [Tooltip("Speed the run clip was authored at. The cycle is scaled against this, not against a number the GameSpec owns.")]
        [SerializeField] private float referenceRunSpeed = 6f;

        private RunnerPlayerController controller;
        private Animator animator;
        private SpriteRenderer bodyRenderer;
        private static readonly int GroundedParameter = Animator.StringToHash("Grounded");
        private static readonly int SlidingParameter = Animator.StringToHash("Sliding");
        private static readonly int RunSpeedParameter = Animator.StringToHash("RunSpeed");

        private Vector3 baseScale;
        private Vector3 baseLocalPosition;
        // Half the sprite's height in world units. The pivot is Center, so a
        // squash lifts the feet by exactly this much times the scale lost.
        private float spriteHalfHeight;

        private Transform leftArm;
        private Transform rightArm;
        private Transform leftLeg;
        private Transform rightLeg;
        private static Sprite limbSprite;

        private float bobPhase;
        private float landTimer;
        private bool wasGrounded = true;

        // Smoothed state, so a frame spike does not pop the character.
        private Vector2 scaleVelocity;
        private Vector2 currentScale = Vector2.one;
        private float currentAngle;
        private float angleVelocity;

        /// <summary>Structural wiring, set at edit time by PrefabGenerator.</summary>
        public void SetController(RunnerPlayerController owner)
        {
            controller = owner;
        }

        /// <summary>
        /// The renderer that carries the character's body. Passed in rather
        /// than searched for, because with a rig the body sprite is two levels
        /// down and this component sits on a transform with no renderer of its
        /// own. Structural wiring, edit time.
        /// </summary>
        public void SetBody(SpriteRenderer body)
        {
            bodyRenderer = body;
        }

        /// <summary>
        /// The rig's Animator. Its presence switches off the procedural limbs
        /// entirely: real clips and four rectangles rotated from LateUpdate
        /// would be two things animating the same character, and they would
        /// disagree. Structural wiring, edit time.
        /// </summary>
        public void SetAnimator(Animator rigAnimator)
        {
            animator = rigAnimator;
        }

        private void Awake()
        {
            baseScale = transform.localScale;
            baseLocalPosition = transform.localPosition;
            if (controller == null) controller = GetComponentInParent<RunnerPlayerController>();
            if (bodyRenderer == null) bodyRenderer = GetComponent<SpriteRenderer>();
            if (animator == null) animator = GetComponentInChildren<Animator>();

            spriteHalfHeight = bodyRenderer != null && bodyRenderer.sprite != null
                ? bodyRenderer.sprite.bounds.extents.y * baseScale.y
                : 0.5f;

            // Only when there is no rig. With one, the limbs are real art on
            // real joints and these grey rectangles would sit on top of them.
            if (animator == null) CreateLimbs(bodyRenderer);
        }

        private void OnDisable()
        {
            // Leave the visual exactly as generated, so a disabled component
            // never bakes a squashed pose into the prefab.
            transform.localScale = baseScale;
            transform.localPosition = baseLocalPosition;
            transform.localRotation = Quaternion.identity;
            SetLimbAngles(0f, 0f, 0f, 0f);
        }

        private void LateUpdate()
        {
            if (controller == null) return;

            float dt = Time.deltaTime;
            if (dt <= 0f) return;

            bool grounded = controller.IsGrounded;
            bool dead = controller.IsDead;

            DriveAnimator(grounded, dead);

            if (grounded && !wasGrounded) landTimer = landRecovery;
            wasGrounded = grounded;
            if (landTimer > 0f) landTimer = Mathf.Max(0f, landTimer - dt);

            Vector2 targetScale = Vector2.one;
            float targetAngle = 0f;
            float bobOffset = 0f;
            float leftArmAngle = 0f;
            float rightArmAngle = 0f;
            float leftLegAngle = 0f;
            float rightLegAngle = 0f;

            if (dead)
            {
                // Flattened and tipped over: the run reads as stopped rather
                // than paused.
                targetScale = new Vector2(1f + landSquash, 1f - landSquash);
                targetAngle = -25f;
            }
            else if (controller.IsSliding)
            {
                // Squashed to exactly the height the collider shrank to, and
                // widened by part of what it lost, so the duck reads as a body
                // compressing rather than a sprite scaled down.
                float squash = 1f - Mathf.Clamp(controller.SlideHeightFraction, 0.2f, 0.95f);
                targetScale = new Vector2(1f + squash * 0.55f, 1f - squash);
                targetAngle = -slideLean;
                bobPhase = 0f;

                // Legs out front, arms swept back - the shape of someone
                // sliding feet-first, and unmistakable against the run cycle.
                leftLegAngle = -slideLegAngle;
                rightLegAngle = -slideLegAngle * 0.82f;
                leftArmAngle = slideArmAngle;
                rightArmAngle = slideArmAngle * 0.82f;
            }
            else if (grounded)
            {
                bobPhase += dt * bobFrequency * Mathf.PI * 2f;
                float bob = Mathf.Sin(bobPhase);

                bobOffset = Mathf.Max(0f, bob) * bobHeight;
                // Squash at the bottom of the arc, where the weight lands.
                float squash = Mathf.Max(0f, -bob) * bobSquash;
                targetScale = new Vector2(1f + squash, 1f - squash);
                targetAngle = -runLean;

                float swing = bob * limbSwing;
                // Diagonal pairs share a phase so the tiny silhouette still
                // reads as a coordinated run rather than four flailing parts.
                leftArmAngle = swing;
                rightLegAngle = swing;
                rightArmAngle = -swing;
                leftLegAngle = -swing;
            }
            else
            {
                float vertical = Mathf.Clamp(
                    controller.VerticalVelocity / velocityForFullStretch, -1f, 1f);
                // Rising stretches, falling squashes - the classic read.
                float stretch = vertical * airStretch;
                targetScale = new Vector2(1f - stretch, 1f + stretch);
                targetAngle = -runLean + vertical * airTilt;
                bobPhase = 0f;

                leftArmAngle = -jumpArmAngle;
                rightArmAngle = jumpArmAngle;
                leftLegAngle = -jumpLegAngle;
                rightLegAngle = jumpLegAngle;
            }

            if (landTimer > 0f)
            {
                float t = landTimer / Mathf.Max(0.0001f, landRecovery);
                float impact = landSquash * t * t;
                targetScale = new Vector2(targetScale.x + impact, targetScale.y - impact);
            }

            currentScale = new Vector2(
                Mathf.SmoothDamp(currentScale.x, targetScale.x, ref scaleVelocity.x, scaleSmoothing),
                Mathf.SmoothDamp(currentScale.y, targetScale.y, ref scaleVelocity.y, scaleSmoothing));
            currentAngle = Mathf.SmoothDampAngle(
                currentAngle, targetAngle, ref angleVelocity, rotationSmoothing);

            transform.localScale = new Vector3(
                baseScale.x * currentScale.x, baseScale.y * currentScale.y, baseScale.z);
            transform.localRotation = Quaternion.Euler(0f, 0f, currentAngle);

            // Anchor the feet. The pivot is the sprite's centre, so squashing
            // to scale s lifts the feet by spriteHalfHeight * (1 - s); without
            // this the character bounces off the ground every time it lands.
            float footCompensation = (1f - currentScale.y) * spriteHalfHeight;
            transform.localPosition = baseLocalPosition
                + new Vector3(0f, bobOffset - footCompensation, 0f);

            SetLimbAngles(leftArmAngle, rightArmAngle, leftLegAngle, rightLegAngle);
        }

        /// <summary>
        /// Hands the rig the three facts its clips branch on. Everything else
        /// this component does - squash, lean, the bob, keeping the feet on
        /// the ground - stays here, on the parent transform, so the two never
        /// write to the same thing.
        /// </summary>
        private void DriveAnimator(bool grounded, bool dead)
        {
            if (animator == null) return;

            animator.SetBool(GroundedParameter, grounded);
            animator.SetBool(SlidingParameter, controller.IsSliding);

            // Cycle speed from how fast the character is really travelling, so
            // a spec that doubles moveSpeed does not leave the legs ambling
            // under a sprinting body. Dead or standing on the title screen,
            // the legs stop rather than running on the spot.
            float speed = dead ? 0f
                : Mathf.Abs(controller.HorizontalVelocity) / Mathf.Max(0.01f, referenceRunSpeed);
            animator.SetFloat(RunSpeedParameter, Mathf.Clamp(speed, 0f, 2.5f));
        }

        private void CreateLimbs(SpriteRenderer bodyRenderer)
        {
            if (bodyRenderer == null || bodyRenderer.sprite == null) return;

            if (limbSprite == null)
            {
                Texture2D texture = new Texture2D(1, 1, TextureFormat.RGBA32, false);
                texture.name = "Runner Limb Texture";
                texture.hideFlags = HideFlags.HideAndDontSave;
                texture.filterMode = FilterMode.Point;
                texture.SetPixel(0, 0, Color.white);
                texture.Apply(false, true);

                limbSprite = Sprite.Create(
                    texture, new Rect(0f, 0f, 1f, 1f), new Vector2(0.5f, 1f), 1f);
                limbSprite.name = "Runner Limb Sprite";
                limbSprite.hideFlags = HideFlags.HideAndDontSave;
            }

            Vector2 bodySize = bodyRenderer.sprite.bounds.size;
            float armWidth = bodySize.x * 0.075f;
            float armLength = bodySize.y * 0.30f;
            float legWidth = bodySize.x * 0.09f;
            float legLength = bodySize.y * 0.30f;

            leftArm = CreateLimb(
                "Left Arm", new Vector2(-bodySize.x * 0.31f, bodySize.y * 0.19f),
                new Vector2(armWidth, armLength), bodyRenderer);
            rightArm = CreateLimb(
                "Right Arm", new Vector2(bodySize.x * 0.31f, bodySize.y * 0.19f),
                new Vector2(armWidth, armLength), bodyRenderer);
            // Joints pushed to the lower edge so the legs extend BELOW the
            // silhouette. Tucked inside it they are covered by the body even
            // when drawn in front, which is half of why nothing was visible.
            leftLeg = CreateLimb(
                "Left Leg", new Vector2(-bodySize.x * 0.16f, -bodySize.y * 0.34f),
                new Vector2(legWidth, legLength), bodyRenderer);
            rightLeg = CreateLimb(
                "Right Leg", new Vector2(bodySize.x * 0.16f, -bodySize.y * 0.34f),
                new Vector2(legWidth, legLength), bodyRenderer);
        }

        private Transform CreateLimb(
            string limbName, Vector2 jointPosition, Vector2 size, SpriteRenderer bodyRenderer)
        {
            GameObject limb = new GameObject(limbName);
            Transform limbTransform = limb.transform;
            limbTransform.SetParent(transform, false);
            limbTransform.localPosition = jointPosition;
            limbTransform.localScale = new Vector3(size.x, size.y, 1f);

            SpriteRenderer limbRenderer = limb.AddComponent<SpriteRenderer>();
            limbRenderer.sprite = limbSprite;
            limbRenderer.color = limbColor;
            limbRenderer.sortingLayerID = bodyRenderer.sortingLayerID;
            // IN FRONT of the body, not behind it. 도리 is an opaque cut-out
            // PNG and the limb joints sit inside its silhouette, so drawing
            // them behind hid every one of them completely - which is why the
            // character read as a sliding decal with no animation at all.
            limbRenderer.sortingOrder = bodyRenderer.sortingOrder + 1;
            return limbTransform;
        }

        private void SetLimbAngles(
            float leftArmDegrees, float rightArmDegrees,
            float leftLegDegrees, float rightLegDegrees)
        {
            if (leftArm == null) return;

            leftArm.localRotation = Quaternion.Euler(0f, 0f, leftArmDegrees);
            rightArm.localRotation = Quaternion.Euler(0f, 0f, rightArmDegrees);
            leftLeg.localRotation = Quaternion.Euler(0f, 0f, leftLegDegrees);
            rightLeg.localRotation = Quaternion.Euler(0f, 0f, rightLegDegrees);
        }
    }
}
