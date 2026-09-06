using System;
using System.Collections.Generic;
using System.IO;
using UnityEditor;
using UnityEditor.Animations;
using UnityEngine;

namespace GameFactory.Editor
{
    /// <summary>
    /// Builds a jointed character out of the separated art in
    /// Assets/Common/Art/Runner/rig/, and generates real Unity animation for
    /// it: AnimationClip assets on an AnimatorController, not angles computed
    /// in a script every frame.
    ///
    /// WHY THE ART HAS TO BE SEPARATE. 도리 is one opaque cut-out PNG with the
    /// paws and feet painted into the silhouette. Nothing in a single sprite
    /// can move relative to anything else, which is exactly what the player
    /// reported: "the image just slides sideways". The rig folder holds the
    /// same drawing taken apart - a body with the limbs removed and the belly
    /// closed over, plus each limb on its own. Gemini draws those (policy
    /// allow_gemini_design); `orchestrator character` is the command.
    ///
    /// WHY CLIPS AND NOT MORE SCRIPT. The previous attempt rotated four white
    /// rectangles from LateUpdate. Clips are inspectable in the Animation
    /// window, tunable without a rebuild, blend between states on their own,
    /// and cost nothing per frame that Unity was not already paying.
    ///
    /// Everything here is additive: with no rig folder, GenerateRig returns
    /// null and the caller keeps the single-sprite character it had.
    /// </summary>
    public static class CharacterRigGenerator
    {
        public const string RigFolder = "Assets/Common/Art/Runner/rig";
        private const string ManifestPath = RigFolder + "/rig.json";
        private const string BodyPart = "body";

        // One full cycle: left foot down, right foot down, back to the start.
        // 0.5s is about 120 steps a minute, which reads as a run at the
        // camera's scroll speed without turning into a blur.
        private const float RunCycleSeconds = 0.5f;

        private const int BodySortingOrder = 10;
        // Limbs draw in FRONT of the body. The paws sit on the belly and the
        // feet overlap its bottom edge, so behind it they are simply invisible
        // - the mistake the procedural version made.
        private const int LimbSortingOrder = 11;

        [Serializable]
        private class RigPoint
        {
            public float x;
            public float y;
        }

        [Serializable]
        private class RigPartEntry
        {
            public string name;
            /// <summary>Where the piece hinges, in fractions of the body sprite, y from the TOP.</summary>
            public RigPoint joint;
            /// <summary>Where that same point falls inside the piece's own image. May sit outside 0-1: a hip is above the foot it swings.</summary>
            public RigPoint anchor;
        }

        [Serializable]
        private class RigManifest
        {
            public string source;
            public string generated;
            /// <summary>"side" or "front". Decides what a run cycle even means - see BuildRunClip.</summary>
            public string view;
            public RigPartEntry[] parts;
        }

        /// <summary>
        /// Which four limbs a rig has, and whether the character is seen from
        /// the side. A side view has a near arm and a far one; a front view
        /// has a left and a right, and they are not the same thing to animate.
        /// </summary>
        private readonly struct RigLayout
        {
            public readonly bool IsSide;
            public readonly string ArmLead;
            public readonly string ArmTrail;
            public readonly string LegLead;
            public readonly string LegTrail;

            public RigLayout(bool isSide, string armLead, string armTrail, string legLead, string legTrail)
            {
                IsSide = isSide;
                ArmLead = armLead;
                ArmTrail = armTrail;
                LegLead = legLead;
                LegTrail = legTrail;
            }

            /// <summary>
            /// Reads the layout off the part names actually present, so a rig
            /// cut from side-view art animates as a stride and one cut from
            /// the front keeps the bounce it had. Missing parts are simply
            /// never keyed - a side view usually hides the far arm entirely,
            /// and that is correct rather than something to work around.
            /// </summary>
            public static RigLayout Resolve(HashSet<string> present, string view)
            {
                bool isSide = string.Equals(view, "side", StringComparison.OrdinalIgnoreCase)
                              || present.Contains("arm_near") || present.Contains("foot_near");

                if (isSide)
                {
                    return new RigLayout(true, "arm_near", "arm_far", "foot_near", "foot_far");
                }
                return new RigLayout(false, "arm_l", "arm_r", "foot_l", "foot_r");
            }
        }

        /// <summary>What the caller needs to wire the character up afterwards.</summary>
        public readonly struct CharacterRig
        {
            public GameObject Root { get; }
            public SpriteRenderer Body { get; }
            public Animator Animator { get; }
            /// <summary>The body sprite's world size - the collider and the ground check are sized from it.</summary>
            public Vector2 BodySize { get; }

            public CharacterRig(GameObject root, SpriteRenderer body, Animator animator, Vector2 bodySize)
            {
                Root = root;
                Body = body;
                Animator = animator;
                BodySize = bodySize;
            }
        }

        /// <summary>True when there is separated art to build a rig from.</summary>
        public static bool RigArtExists()
        {
            return File.Exists(EditorPaths.ToAbsolutePath(ManifestPath));
        }

        /// <summary>
        /// Builds the rig under <paramref name="parent"/> and returns it, or
        /// null when the art or the manifest is not usable. Returning null is
        /// not an error - it is how a project with no rig art keeps working.
        /// </summary>
        public static CharacterRig? GenerateRig(Transform parent, string assetFolder)
        {
            RigManifest manifest = ReadManifest();
            if (manifest == null || manifest.parts == null || manifest.parts.Length == 0) return null;

            Sprite bodySprite = LoadPart(BodyPart);
            if (bodySprite == null)
            {
                Debug.LogWarning($"[CharacterRigGenerator] {ManifestPath} lists parts but " +
                                 $"{RigFolder}/{BodyPart}.png is missing; falling back to the single sprite.");
                return null;
            }

            GameObject rigRoot = new GameObject("Rig");
            rigRoot.transform.SetParent(parent, false);

            SpriteRenderer bodyRenderer = new GameObject("Body").AddComponent<SpriteRenderer>();
            bodyRenderer.transform.SetParent(rigRoot.transform, false);
            bodyRenderer.sprite = bodySprite;
            bodyRenderer.sortingOrder = BodySortingOrder;

            Vector2 bodySize = bodySprite.bounds.size;
            var joints = new Dictionary<string, Transform>();

            foreach (RigPartEntry entry in manifest.parts)
            {
                if (entry == null || string.IsNullOrEmpty(entry.name)) continue;
                if (entry.name == BodyPart) continue;
                if (entry.joint == null || entry.anchor == null) continue;

                Sprite sprite = LoadPart(entry.name);
                if (sprite == null)
                {
                    Debug.LogWarning($"[CharacterRigGenerator] rig part '{entry.name}' has no PNG; skipped.");
                    continue;
                }

                // The joint is an empty transform - it is what rotates. The
                // sprite hangs off it at a fixed offset, so rotation happens
                // about the shoulder or hip rather than the middle of the art.
                GameObject joint = new GameObject(JointName(entry.name));
                joint.transform.SetParent(rigRoot.transform, false);
                joint.transform.localPosition = new Vector3(
                    (entry.joint.x - 0.5f) * bodySize.x,
                    (0.5f - entry.joint.y) * bodySize.y,
                    0f);

                SpriteRenderer limb = new GameObject("Sprite").AddComponent<SpriteRenderer>();
                limb.transform.SetParent(joint.transform, false);
                Vector2 partSize = sprite.bounds.size;
                limb.transform.localPosition = new Vector3(
                    (0.5f - entry.anchor.x) * partSize.x,
                    (entry.anchor.y - 0.5f) * partSize.y,
                    0f);
                limb.sprite = sprite;
                limb.sortingOrder = IsFarSide(entry.name)
                    ? BodySortingOrder - 1
                    : LimbSortingOrder;

                joints[entry.name] = joint.transform;
            }

            if (joints.Count == 0)
            {
                // A body on its own is what the single sprite already was.
                UnityEngine.Object.DestroyImmediate(rigRoot);
                return null;
            }

            Animator animator = rigRoot.AddComponent<Animator>();
            animator.runtimeAnimatorController =
                BuildController(assetFolder, joints.Keys, manifest.view);
            animator.applyRootMotion = false;
            // The clips only move children of this object; culling by a
            // renderer Unity cannot find on the Animator itself would stop
            // them the moment the body scrolled past the frustum edge.
            animator.cullingMode = AnimatorCullingMode.AlwaysAnimate;

            return new CharacterRig(rigRoot, bodyRenderer, animator, bodySize);
        }

        private static string JointName(string partName)
        {
            switch (partName)
            {
                case "arm_l": return "ArmLeft";
                case "arm_r": return "ArmRight";
                case "foot_l": return "FootLeft";
                case "foot_r": return "FootRight";
                case "arm_near": return "ArmNear";
                case "arm_far": return "ArmFar";
                case "foot_near": return "FootNear";
                case "foot_far": return "FootFar";
                default: return partName;
            }
        }

        /// <summary>
        /// A limb on the far side of a character seen in profile. It draws
        /// BEHIND the body, which is the whole reason it reads as depth - the
        /// part of it that shows past the silhouette is the part that sells
        /// the stride. In a front view nothing is far, and everything stays in
        /// front, where the earlier attempt got this exactly wrong.
        /// </summary>
        private static bool IsFarSide(string partName)
        {
            return partName.EndsWith("_far", StringComparison.Ordinal);
        }

        private static Sprite LoadPart(string partName)
        {
            return AssetDatabase.LoadAssetAtPath<Sprite>($"{RigFolder}/{partName}.png");
        }

        private static RigManifest ReadManifest()
        {
            string absolute = EditorPaths.ToAbsolutePath(ManifestPath);
            if (!File.Exists(absolute)) return null;

            try
            {
                return JsonUtility.FromJson<RigManifest>(File.ReadAllText(absolute));
            }
            catch (ArgumentException e)
            {
                Debug.LogWarning($"[CharacterRigGenerator] {ManifestPath} could not be read: {e.Message}");
                return null;
            }
        }

        // ---- animation -------------------------------------------------------

        private static RuntimeAnimatorController BuildController(
            string assetFolder, IEnumerable<string> partNames, string view)
        {
            string folder = $"{assetFolder}/Animation";
            if (!AssetDatabase.IsValidFolder(folder))
            {
                // Created on disk AND refreshed: CreateAsset writes into the
                // asset database, which will not accept a path under a folder
                // it has never heard of.
                Directory.CreateDirectory(EditorPaths.ToAbsolutePath(folder));
                AssetDatabase.Refresh();
            }

            var present = new HashSet<string>(partNames);
            RigLayout layout = RigLayout.Resolve(present, view);

            AnimationClip run = BuildRunClip(folder, present, layout);
            AnimationClip air = BuildPoseClip(folder, "Character_Air", present, layout,
                armDegrees: -34f, legDegrees: -20f, legLift: 0.05f);
            AnimationClip slide = BuildPoseClip(folder, "Character_Slide", present, layout,
                armDegrees: 46f, legDegrees: 62f, legLift: 0f);

            // Deleted first, or a second generation pass leaves
            // "Character 1.controller" beside the one the prefab points at.
            string controllerPath = $"{folder}/Character.controller";
            AssetDatabase.DeleteAsset(controllerPath);
            AnimatorController controller =
                AnimatorController.CreateAnimatorControllerAtPath(controllerPath);
            controller.AddParameter("Grounded", AnimatorControllerParameterType.Bool);
            controller.AddParameter("Sliding", AnimatorControllerParameterType.Bool);
            controller.AddParameter("RunSpeed", AnimatorControllerParameterType.Float);

            AnimatorStateMachine machine = controller.layers[0].stateMachine;

            AnimatorState runState = machine.AddState("Run");
            runState.motion = run;
            // The run cycle keeps time with how fast the player is actually
            // moving, so a spec that doubles moveSpeed does not leave the legs
            // ambling underneath a sprinting character.
            runState.speedParameterActive = true;
            runState.speedParameter = "RunSpeed";

            AnimatorState airState = machine.AddState("Air");
            airState.motion = air;

            AnimatorState slideState = machine.AddState("Slide");
            slideState.motion = slide;

            machine.defaultState = runState;

            // Any-state transitions rather than a web of pairwise ones: the
            // three poses are mutually exclusive readings of two bools, and
            // spelling that out once per destination cannot leave a hole.
            AddAnyTransition(machine, slideState, ("Sliding", true));
            AddAnyTransition(machine, airState, ("Sliding", false), ("Grounded", false));
            AddAnyTransition(machine, runState, ("Sliding", false), ("Grounded", true));

            AssetDatabase.SaveAssets();
            return controller;
        }

        private static void AddAnyTransition(AnimatorStateMachine machine, AnimatorState destination,
            params (string parameter, bool value)[] conditions)
        {
            AnimatorStateTransition transition = machine.AddAnyStateTransition(destination);
            transition.hasExitTime = false;
            transition.duration = 0.08f;
            // Without this the state re-enters itself every frame its
            // conditions hold, and the clip never advances past frame one.
            transition.canTransitionToSelf = false;
            foreach ((string parameter, bool value) in conditions)
            {
                transition.AddCondition(
                    value ? AnimatorConditionMode.If : AnimatorConditionMode.IfNot, 0f, parameter);
            }
        }

        private const string RotationProperty = "localEulerAnglesRaw.z";
        private const string LiftProperty = "m_LocalPosition.y";
        /// <summary>Forward and back travel. In profile this carries the stride, not the rotation.</summary>
        private const string StrideProperty = "m_LocalPosition.x";

        private static AnimationClip BuildRunClip(string folder, HashSet<string> present,
                                                  RigLayout layout)
        {
            AnimationClip clip = new AnimationClip { name = "Character_Run" };

            if (layout.IsSide)
            {
                // A PROFILE CAN ACTUALLY RUN, but this drawing has no legs -
                // the body meets the feet directly. Pivoting from a hip up
                // inside the belly swung the feet clear of the body and they
                // read as detached, so the stride is carried by TRAVEL and
                // LIFT, with rotation only tilting the foot as it leaves the
                // ground. Verified by rendering the cycle over the real art
                // before any of it was written here.
                const float ArmSwing = 30f;
                const float FootTilt = 14f;
                // World units. The character is 1.5 tall over 192 px, so these
                // are the 4.5 px of travel and 5 px of lift that were checked.
                const float Stride = 0.0352f;
                const float LegLift = 0.0391f;

                AddSwing(clip, layout.ArmLead, present, RotationProperty, ArmSwing, 0.5f);
                // Behind the body and opposite the near one, so the two never
                // overlap into a single shape. Usually absent in profile art.
                AddSwing(clip, layout.ArmTrail, present, RotationProperty, ArmSwing, 0f);

                AddSideLeg(clip, layout.LegTrail, present, FootTilt, Stride, LegLift, 0f);
                AddSideLeg(clip, layout.LegLead, present, FootTilt, Stride, LegLift, 0.5f);
            }
            else
            {
                // A front-facing character does not stride: seen from the
                // front, a run is a bounce with the feet alternating up and
                // down. Swinging them sideways reads as splaying the legs
                // apart, not as running.
                const float ArmSwing = 24f;
                const float FootTilt = 11f;
                const float LegLift = 0.055f;

                // Arms mirror: the left arm's forward is +Z and the right
                // arm's is -Z, so opposite signs at the same phase IS the
                // alternation.
                AddSwing(clip, layout.ArmLead, present, RotationProperty, ArmSwing, 0f);
                AddSwing(clip, layout.ArmTrail, present, RotationProperty, -ArmSwing, 0f);

                // Half-waves, and both tilt the same way: a foot tilts while
                // it is off the ground and sits flat while it carries weight.
                // A planted foot that rolls is the tell that a cycle was
                // written as maths rather than watched.
                AddHalfWave(clip, layout.LegLead, present, RotationProperty, FootTilt, 0f);
                AddHalfWave(clip, layout.LegTrail, present, RotationProperty, FootTilt, 0.5f);
                AddHalfWave(clip, layout.LegLead, present, LiftProperty, LegLift, 0f);
                AddHalfWave(clip, layout.LegTrail, present, LiftProperty, LegLift, 0.5f);
                AddConstant(clip, layout.LegLead, present, StrideProperty, 0f);
                AddConstant(clip, layout.LegTrail, present, StrideProperty, 0f);
            }

            SetLooping(clip, true);
            return SaveClip(clip, folder);
        }

        private static AnimationClip BuildPoseClip(string folder, string clipName,
            HashSet<string> present, RigLayout layout,
            float armDegrees, float legDegrees, float legLift)
        {
            AnimationClip clip = new AnimationClip { name = clipName };

            // In profile both arms and both legs are on the same axis, so a
            // pose points them the same way. Mirrored, they would splay.
            float trailArm = layout.IsSide ? armDegrees * 0.6f : -armDegrees;
            float trailLeg = layout.IsSide ? legDegrees * 0.6f : -legDegrees;

            AddConstant(clip, layout.ArmLead, present, RotationProperty, armDegrees);
            AddConstant(clip, layout.ArmTrail, present, RotationProperty, trailArm);
            AddConstant(clip, layout.LegLead, present, RotationProperty, legDegrees);
            AddConstant(clip, layout.LegTrail, present, RotationProperty, trailLeg);

            // Written even when it is zero. A property this clip leaves alone
            // keeps whatever the previous state last animated it to, so a
            // slide entered mid-stride would hold one foot in the air.
            AddConstant(clip, layout.LegLead, present, LiftProperty, legLift);
            AddConstant(clip, layout.LegTrail, present, LiftProperty, legLift);
            AddConstant(clip, layout.LegLead, present, StrideProperty, 0f);
            AddConstant(clip, layout.LegTrail, present, StrideProperty, 0f);

            SetLooping(clip, true);
            return SaveClip(clip, folder);
        }

        /// <summary>
        /// One leg of a profile stride: it travels forward and back, lifts
        /// while it is forward, and tilts as it leaves the ground. The tilt
        /// opposes the travel - a foot swinging forward rolls its toe up.
        /// </summary>
        private static void AddSideLeg(AnimationClip clip, string part, HashSet<string> present,
            float tiltDegrees, float stride, float lift, float phase)
        {
            AddSwing(clip, part, present, RotationProperty, -tiltDegrees, phase);
            AddSwing(clip, part, present, StrideProperty, stride, phase);
            AddHalfWave(clip, part, present, LiftProperty, lift, phase);
        }

        /// <summary>A full sine over the cycle, offset by <paramref name="phase"/> of it.</summary>
        private static void AddSwing(AnimationClip clip, string part, HashSet<string> present,
            string property, float amplitude, float phase)
        {
            AddCurve(clip, part, present, property, phase, 4,
                     wave => amplitude * wave);
        }

        /// <summary>Half a sine: the limb acts for half the cycle and rests flat for the other half.</summary>
        private static void AddHalfWave(AnimationClip clip, string part, HashSet<string> present,
            string property, float amplitude, float phase)
        {
            AddCurve(clip, part, present, property, phase, 8,
                     wave => Mathf.Max(0f, wave) * amplitude);
        }

        private static void AddCurve(AnimationClip clip, string part, HashSet<string> present,
            string property, float phase, int segments, Func<float, float> shape)
        {
            if (!present.Contains(part)) return;

            var curve = new AnimationCurve();
            for (int i = 0; i <= segments; i++)
            {
                float t = (float)i / segments;
                float wave = Mathf.Sin((t + phase) * 2f * Mathf.PI);
                curve.AddKey(new Keyframe(t * RunCycleSeconds, shape(wave)));
            }
            Smooth(curve);
            clip.SetCurve(JointName(part), typeof(Transform), property, curve);
        }

        private static void AddConstant(AnimationClip clip, string part, HashSet<string> present,
            string property, float value)
        {
            if (!present.Contains(part)) return;

            var curve = new AnimationCurve();
            curve.AddKey(new Keyframe(0f, value));
            curve.AddKey(new Keyframe(RunCycleSeconds, value));
            clip.SetCurve(JointName(part), typeof(Transform), property, curve);
        }

        private static void Smooth(AnimationCurve curve)
        {
            for (int i = 0; i < curve.length; i++) curve.SmoothTangents(i, 0f);
        }

        private static void SetLooping(AnimationClip clip, bool looping)
        {
            AnimationClipSettings settings = AnimationUtility.GetAnimationClipSettings(clip);
            settings.loopTime = looping;
            AnimationUtility.SetAnimationClipSettings(clip, settings);
        }

        private static AnimationClip SaveClip(AnimationClip clip, string folder)
        {
            string path = $"{folder}/{clip.name}.anim";
            AssetDatabase.DeleteAsset(path);
            AssetDatabase.CreateAsset(clip, path);
            return AssetDatabase.LoadAssetAtPath<AnimationClip>(path);
        }
    }
}
