using System.Collections.Generic;
using System.Globalization;
using System.IO;
using System.Text;
using UnityEditor;
using UnityEngine;

namespace GameFactory.Editor
{
    /// <summary>
    /// Cuts the one-piece character drawing into the separate images a rig
    /// needs - inside Unity, with no key and no service.
    ///
    /// WHAT IT PRODUCES. body.png (the character with both paws and both feet
    /// removed and the belly closed over them), one image per limb, and
    /// rig.json saying where each piece hinges. CharacterRigGenerator turns
    /// that into joints and AnimationClips.
    ///
    /// THE HARD PART IS THE BODY, NOT THE LIMBS. Cutting a paw out is a
    /// rectangle. What is left behind is a rectangular bite out of the belly,
    /// and the moment that paw swings the player sees a hole. So the removed
    /// area is repainted from the fur around it by a pull-push fill, weighted
    /// by alpha - without that weighting the transparent pixels outside the
    /// silhouette average in as white and the belly gets a pale rectangle.
    ///
    /// The two kinds of removal are not the same. A paw sits INSIDE the
    /// outline, so the body keeps its original alpha there and only the colour
    /// is repainted. The feet stick out BELOW the belly, so there the outline
    /// itself has to be rebuilt or the body ends in a ragged fade where the
    /// legs used to be.
    ///
    /// A DRAWING ON PAPER IS PREPARED FIRST. player_side.png arrived flattened
    /// onto a white sheet with a shadow under the feet. It is cut off that
    /// background, trimmed, and scaled to the height the rest of the pipeline
    /// expects, because every fraction in the tables below refers to the
    /// animal and not to the paper around it.
    ///
    /// THE NUMBERS ARE MEASURED, NOT GUESSED, and each view has its own table.
    /// A profile is preferred whenever one exists: the game scrolls sideways,
    /// and a character facing the camera can only bounce on the spot.
    /// Different art means different numbers, and those tables are where they
    /// change - not the code around them.
    /// </summary>
    public static class CharacterPartSlicer
    {
        public const string SourceSpritePath = "Assets/Common/Art/Runner/player.png";

        /// <summary>
        /// The character in profile. Preferred over the front view whenever it
        /// exists, because the game scrolls sideways: a character facing the
        /// camera can only bounce on the spot, while a profile has a stride.
        /// </summary>
        public const string SideSpritePath = "Assets/Common/Art/Runner/player_side.png";

        public const string RigFolder = "Assets/Common/Art/Runner/rig";
        public const string ManifestPath = RigFolder + "/rig.json";

        /// <summary>Character height in pixels after preparation, matching player.png at 128 PPU - 1.5 world units.</summary>
        private const int TargetHeight = 192;

        /// <summary>Written into rig.json so it is always clear who drew what is on disk.</summary>
        public const string SourceName = "unity-slicer";

        /// <summary>
        /// Sources whose output this may replace on its own. Anything else -
        /// notably art drawn by Gemini, or corrected by hand - is left alone
        /// unless a person asks for a re-slice from the menu.
        /// </summary>
        private const string ReplaceableSource = "local-slicer";

        private const float FeatherPixels = 2f;
        private const int InpaintIterations = 90;
        /// <summary>Alpha above which a rebuilt outline counts as solid, 0-255.</summary>
        private const float SilhouetteThreshold = 110f;
        /// <summary>Red-to-blue spread below which a pixel counts as grey rather than fur, 0-255.</summary>
        private const float NeutralSaturation = 14f;
        /// <summary>Brightness above which a grey pixel is paper rather than an eye or a nose, 0-255.</summary>
        private const float NeutralBrightness = 140f;
        /// <summary>Transparent margin kept around a cut piece so its soft edge is not clipped.</summary>
        private const int CropPadding = 3;

        private readonly struct PartCut
        {
            /// <summary>Rectangle to remove, in fractions of the image, y measured from the TOP.</summary>
            public readonly string Name;
            public readonly float X0;
            public readonly float Y0;
            public readonly float X1;
            public readonly float Y1;

            /// <summary>True when removing this piece leaves a gap in the outline that must be closed.</summary>
            public readonly bool RebuildSilhouette;

            /// <summary>Where the piece turns, same coordinates as the rectangle.</summary>
            public readonly float JointX;
            public readonly float JointY;

            public PartCut(string name, float x0, float y0, float x1, float y1,
                           bool rebuildSilhouette, float jointX, float jointY)
            {
                Name = name;
                X0 = x0;
                Y0 = y0;
                X1 = x1;
                Y1 = y1;
                RebuildSilhouette = rebuildSilhouette;
                JointX = jointX;
                JointY = jointY;
            }
        }

        /// <summary>Measured off player.png (136x192), the character facing the camera.</summary>
        private static readonly PartCut[] FrontCuts =
        {
            new PartCut("arm_l", 0.152f, 0.618f, 0.242f, 0.782f, false, 0.197f, 0.612f),
            new PartCut("arm_r", 0.752f, 0.618f, 0.842f, 0.782f, false, 0.797f, 0.612f),
            new PartCut("foot_l", 0.178f, 0.872f, 0.350f, 1.000f, true, 0.264f, 0.795f),
            new PartCut("foot_r", 0.636f, 0.872f, 0.808f, 1.000f, true, 0.722f, 0.795f),
        };

        /// <summary>
        /// Measured off player_side.png once prepared (137x192), the character
        /// in profile facing right. Only ONE arm is cut: in profile the far one
        /// is behind the body and simply not drawn, which is what almost every
        /// 2D runner ships.
        ///
        /// The joints sit at each foot's own ankle rather than up inside the
        /// belly. There is no leg in this drawing - the body meets the feet
        /// directly - so pivoting from a hip swung the feet away from the body
        /// and they read as detached. From the ankle, with the stride carried
        /// mostly by travel and lift rather than rotation, they stay attached.
        /// </summary>
        private static readonly PartCut[] SideCuts =
        {
            new PartCut("arm_near", 0.695f, 0.625f, 0.828f, 0.762f, false, 0.760f, 0.618f),
            new PartCut("foot_near", 0.575f, 0.878f, 0.792f, 1.000f, true, 0.665f, 0.880f),
            new PartCut("foot_far", 0.415f, 0.893f, 0.628f, 0.978f, true, 0.520f, 0.895f),
        };

        [MenuItem("Game Factory/Character/Slice the character into rig parts")]
        private static void SliceFromMenu()
        {
            ResolveSource(out string sourcePath, out string view, out PartCut[] cuts);
            if (Slice(force: true))
            {
                Debug.Log($"[CharacterPartSlicer] Cut {sourcePath} ({view}) into "
                          + $"{cuts.Length + 1} parts in {RigFolder}.");
            }
        }

        /// <summary>
        /// Makes the rig art if it is missing, or if what is there was cut by
        /// an earlier version of this. Called by the prefab generator, so a
        /// plain build produces a character that can move without anyone
        /// running a menu item first. Returns true when parts are on disk.
        /// </summary>
        public static bool EnsureRigParts()
        {
            return Slice(force: false);
        }

        /// <summary>The drawing to cut, and what view it is. Profile wins when it exists.</summary>
        private static bool ResolveSource(out string assetPath, out string view, out PartCut[] cuts)
        {
            if (File.Exists(EditorPaths.ToAbsolutePath(SideSpritePath)))
            {
                assetPath = SideSpritePath;
                view = "side";
                cuts = SideCuts;
                return true;
            }
            assetPath = SourceSpritePath;
            view = "front";
            cuts = FrontCuts;
            return File.Exists(EditorPaths.ToAbsolutePath(assetPath));
        }

        public static bool Slice(bool force)
        {
            if (!ResolveSource(out string sourcePath, out string view, out PartCut[] cuts))
            {
                // Not an error: a project with no character art keeps the
                // single-sprite player it always had.
                return false;
            }

            if (!force && !ShouldReplaceExisting(view)) return File.Exists(EditorPaths.ToAbsolutePath(ManifestPath));

            // Loaded from the file rather than through the imported Sprite, so
            // the texture is readable whatever Read/Write Enabled is set to on
            // the asset - flipping that flag would be a side effect on a file
            // this generator does not own.
            Texture2D source = new Texture2D(2, 2, TextureFormat.RGBA32, false);
            if (!source.LoadImage(File.ReadAllBytes(EditorPaths.ToAbsolutePath(sourcePath))))
            {
                Debug.LogWarning($"[CharacterPartSlicer] {sourcePath} could not be decoded.");
                Object.DestroyImmediate(source);
                return false;
            }

            int width = source.width;
            int height = source.height;
            Pixels image = Pixels.FromTexture(source);
            Object.DestroyImmediate(source);

            // A drawing that arrives on a white sheet has to be cut out and
            // scaled before anything is measured against it, or every fraction
            // in the tables above refers to the paper as well as the animal.
            image = Prepare(image, ref width, ref height);

            float[] combined = new float[width * height];
            float[] rebuildMask = new float[width * height];
            float[][] masks = new float[cuts.Length][];

            for (int i = 0; i < cuts.Length; i++)
            {
                masks[i] = BuildSoftMask(cuts[i], width, height);
                for (int p = 0; p < combined.Length; p++)
                {
                    combined[p] = Mathf.Min(1f, combined[p] + masks[i][p]);
                    if (cuts[i].RebuildSilhouette)
                    {
                        rebuildMask[p] = Mathf.Min(1f, rebuildMask[p] + masks[i][p]);
                    }
                }
            }

            Directory.CreateDirectory(EditorPaths.ToAbsolutePath(RigFolder));

            WriteBody(image, combined, rebuildMask, width, height);
            var entries = new StringBuilder();
            AppendEntry(entries, "body", 0.5f, 0.5f, 0.5f, 0.5f, first: true);

            for (int i = 0; i < cuts.Length; i++)
            {
                WritePiece(image, masks[i], cuts[i], width, height, entries);
            }

            File.WriteAllText(EditorPaths.ToAbsolutePath(ManifestPath),
                              BuildManifest(entries.ToString(), sourcePath, view), new UTF8Encoding(false));

            AssetDatabase.Refresh();
            return true;
        }

        // ---- preparing a drawing that arrived on paper ------------------------

        /// <summary>
        /// Cuts the character off its background, trims to what is left, and
        /// scales it to the height the rest of the pipeline expects. An image
        /// that already has transparency is passed straight through - this is
        /// only for a drawing delivered flattened onto a sheet.
        /// </summary>
        private static Pixels Prepare(Pixels image, ref int width, ref int height)
        {
            bool hasTransparency = false;
            for (int p = 0; p < image.A.Length; p++)
            {
                if (image.A[p] < 250f) { hasTransparency = true; break; }
            }
            if (hasTransparency) return image;

            RemoveNeutralBackground(image, width, height);
            image = TrimToContent(image, ref width, ref height);
            return ScaleToHeight(image, ref width, ref height, TargetHeight);
        }

        /// <summary>
        /// Sets alpha to zero on the paper and on the drop shadow, and leaves
        /// everything else alone.
        ///
        /// Neutral AND bright is the test, not "close to white". The character
        /// is warm - every fur and spine pixel has a red-blue spread - while
        /// the sheet and the soft shadow under the feet are both grey. Matching
        /// on colour distance alone kept the shadow, which the game would then
        /// have drawn on top of its own ground. Brightness is what spares the
        /// eye and the nose, which are neutral too but dark.
        ///
        /// Only background CONNECTED TO THE BORDER is removed, so a pale
        /// highlight inside the character is never punched out.
        /// </summary>
        private static void RemoveNeutralBackground(Pixels image, int width, int height)
        {
            int count = width * height;
            bool[] neutral = new bool[count];
            for (int p = 0; p < count; p++)
            {
                float max = Mathf.Max(image.R[p], Mathf.Max(image.G[p], image.B[p]));
                float min = Mathf.Min(image.R[p], Mathf.Min(image.G[p], image.B[p]));
                neutral[p] = (max - min) < NeutralSaturation && max > NeutralBrightness;
            }

            bool[] background = new bool[count];
            var stack = new Stack<int>();
            for (int x = 0; x < width; x++)
            {
                Seed(stack, background, neutral, x);
                Seed(stack, background, neutral, (height - 1) * width + x);
            }
            for (int y = 0; y < height; y++)
            {
                Seed(stack, background, neutral, y * width);
                Seed(stack, background, neutral, y * width + width - 1);
            }

            while (stack.Count > 0)
            {
                int p = stack.Pop();
                int x = p % width;
                int y = p / width;
                if (x > 0) Seed(stack, background, neutral, p - 1);
                if (x < width - 1) Seed(stack, background, neutral, p + 1);
                if (y > 0) Seed(stack, background, neutral, p - width);
                if (y < height - 1) Seed(stack, background, neutral, p + width);
            }

            float[] inside = new float[count];
            for (int p = 0; p < count; p++) inside[p] = background[p] ? 0f : 1f;

            // Two box blurs stand in for a one-pixel gaussian, then a steep
            // ramp: the edge gets a pixel of softness without the outline
            // turning to mush.
            float[] soft = Blur(Blur(inside, width, height), width, height);
            for (int p = 0; p < count; p++)
            {
                image.A[p] = Mathf.Clamp01((soft[p] - 0.35f) / 0.45f) * 255f;
            }
        }

        private static void Seed(Stack<int> stack, bool[] background, bool[] neutral, int index)
        {
            if (background[index] || !neutral[index]) return;
            background[index] = true;
            stack.Push(index);
        }

        private static Pixels TrimToContent(Pixels image, ref int width, ref int height)
        {
            int minX = width, minY = height, maxX = -1, maxY = -1;
            for (int y = 0; y < height; y++)
            {
                for (int x = 0; x < width; x++)
                {
                    if (image.A[y * width + x] <= 0f) continue;
                    if (x < minX) minX = x;
                    if (x > maxX) maxX = x;
                    if (y < minY) minY = y;
                    if (y > maxY) maxY = y;
                }
            }
            if (maxX < minX || maxY < minY) return image;

            int newWidth = maxX - minX + 1;
            int newHeight = maxY - minY + 1;
            Pixels trimmed = Pixels.Empty(newWidth * newHeight);
            for (int y = 0; y < newHeight; y++)
            {
                for (int x = 0; x < newWidth; x++)
                {
                    int s = (minY + y) * width + (minX + x);
                    int t = y * newWidth + x;
                    trimmed.R[t] = image.R[s];
                    trimmed.G[t] = image.G[s];
                    trimmed.B[t] = image.B[s];
                    trimmed.A[t] = image.A[s];
                }
            }
            width = newWidth;
            height = newHeight;
            return trimmed;
        }

        /// <summary>
        /// Area-averaged downscale, weighted by alpha. Averaging raw colour
        /// would pull the transparent pixels just outside the outline into
        /// every edge pixel and leave a pale fringe all the way round.
        /// </summary>
        private static Pixels ScaleToHeight(Pixels image, ref int width, ref int height, int targetHeight)
        {
            if (height == targetHeight) return image;

            int targetWidth = Mathf.Max(1, Mathf.RoundToInt(width * (float)targetHeight / height));
            Pixels scaled = Pixels.Empty(targetWidth * targetHeight);

            for (int y = 0; y < targetHeight; y++)
            {
                int y0 = y * height / targetHeight;
                int y1 = Mathf.Max(y0 + 1, (y + 1) * height / targetHeight);
                for (int x = 0; x < targetWidth; x++)
                {
                    int x0 = x * width / targetWidth;
                    int x1 = Mathf.Max(x0 + 1, (x + 1) * width / targetWidth);

                    float weight = 0f, r = 0f, g = 0f, b = 0f, a = 0f, samples = 0f;
                    for (int sy = y0; sy < y1; sy++)
                    {
                        for (int sx = x0; sx < x1; sx++)
                        {
                            int s = sy * width + sx;
                            float w = image.A[s] / 255f;
                            r += image.R[s] * w;
                            g += image.G[s] * w;
                            b += image.B[s] * w;
                            a += image.A[s];
                            weight += w;
                            samples += 1f;
                        }
                    }

                    int t = y * targetWidth + x;
                    float divisor = Mathf.Max(weight, 1e-6f);
                    scaled.R[t] = r / divisor;
                    scaled.G[t] = g / divisor;
                    scaled.B[t] = b / divisor;
                    scaled.A[t] = a / Mathf.Max(samples, 1f);
                }
            }

            width = targetWidth;
            height = targetHeight;
            return scaled;
        }

        /// <summary>
        /// True when there is nothing on disk, or what is there was cut by this
        /// tool and can safely be cut again. Art from another source is never
        /// overwritten by an automatic run.
        /// </summary>
        private static bool ShouldReplaceExisting(string view)
        {
            string absolute = EditorPaths.ToAbsolutePath(ManifestPath);
            if (!File.Exists(absolute)) return true;

            string text = File.ReadAllText(absolute);
            bool ours = text.Contains($"\"source\": \"{SourceName}\"")
                        || text.Contains($"\"source\": \"{ReplaceableSource}\"");
            if (!ours) return false;

            // Art from the same tool but of the OTHER view is stale the moment
            // a profile drawing appears next to the front one: its joints refer
            // to a body that is no longer the one being drawn.
            return !text.Contains($"\"view\": \"{view}\"");
        }

        // ---- the cut ---------------------------------------------------------

        /// <summary>
        /// A rectangle with a soft edge. A hard one leaves a visible seam where
        /// the piece meets the body it was cut from.
        /// </summary>
        private static float[] BuildSoftMask(PartCut cut, int width, int height)
        {
            float left = cut.X0 * width;
            float right = cut.X1 * width;
            float top = cut.Y0 * height;
            float bottom = cut.Y1 * height;

            float[] mask = new float[width * height];
            for (int y = 0; y < height; y++)
            {
                float vertical = Ramp(y - top) * Ramp(bottom - y);
                if (vertical <= 0f) continue;

                for (int x = 0; x < width; x++)
                {
                    mask[y * width + x] = Ramp(x - left) * Ramp(right - x) * vertical;
                }
            }
            return mask;
        }

        private static float Ramp(float distance)
        {
            return Mathf.Clamp01(distance / FeatherPixels);
        }

        private static void WriteBody(Pixels image, float[] hole, float[] rebuild, int width, int height)
        {
            float[] filledR = (float[])image.R.Clone();
            float[] filledG = (float[])image.G.Clone();
            float[] filledB = (float[])image.B.Clone();
            float[] filledA = (float[])image.A.Clone();

            // Colour, weighted by alpha. Unweighted, the transparent pixels
            // beyond the silhouette average in as white and the repaint comes
            // out as a pale rectangle on the belly.
            float[] weight = new float[hole.Length];
            for (int p = 0; p < hole.Length; p++)
            {
                bool cut = hole[p] > 0.02f;
                weight[p] = cut ? 0f : image.A[p] / 255f;
                if (!cut) continue;
                filledR[p] = 0f;
                filledG[p] = 0f;
                filledB[p] = 0f;
            }
            PullPush(filledR, filledG, filledB, weight, hole, image, width, height);

            // Alpha gets its own fill, and it is only used where a removed
            // piece left a gap in the outline.
            float[] alphaFill = (float[])image.A.Clone();
            float[] alphaWeight = new float[hole.Length];
            for (int p = 0; p < hole.Length; p++)
            {
                bool cut = hole[p] > 0.02f;
                alphaWeight[p] = cut ? 0f : 1f;
                if (cut) alphaFill[p] = 0f;
            }
            PullPushSingle(alphaFill, alphaWeight, hole, image.A, width, height);

            float[] hardened = new float[hole.Length];
            for (int p = 0; p < hole.Length; p++)
            {
                hardened[p] = alphaFill[p] > SilhouetteThreshold ? 255f : 0f;
            }
            // Twice, for two pixels of anti-aliasing back onto a hard edge.
            hardened = Blur(hardened, width, height);
            hardened = Blur(hardened, width, height);

            for (int p = 0; p < hole.Length; p++)
            {
                filledA[p] = rebuild[p] > 0.02f ? Mathf.Clamp(hardened[p], 0f, 255f) : image.A[p];
            }

            WritePng($"{RigFolder}/body.png",
                     new Pixels(filledR, filledG, filledB, filledA), width, height,
                     0, 0, width, height);
        }

        private static void WritePiece(Pixels image, float[] mask, PartCut cut,
                                       int width, int height, StringBuilder entries)
        {
            float[] alpha = new float[mask.Length];
            for (int p = 0; p < mask.Length; p++) alpha[p] = image.A[p] * mask[p];

            int x0 = Mathf.Max(0, Mathf.FloorToInt(cut.X0 * width) - CropPadding);
            int y0 = Mathf.Max(0, Mathf.FloorToInt(cut.Y0 * height) - CropPadding);
            int x1 = Mathf.Min(width, Mathf.CeilToInt(cut.X1 * width) + CropPadding);
            int y1 = Mathf.Min(height, Mathf.CeilToInt(cut.Y1 * height) + CropPadding);
            int cropWidth = Mathf.Max(1, x1 - x0);
            int cropHeight = Mathf.Max(1, y1 - y0);

            WritePng($"{RigFolder}/{cut.Name}.png",
                     new Pixels(image.R, image.G, image.B, alpha), width, height,
                     x0, y0, cropWidth, cropHeight);

            // Where the joint falls inside this piece's own image. It may sit
            // outside 0-1 and that is correct: a hip is above the foot it
            // swings, so the anchor is above the top edge of the foot's image.
            float anchorX = (cut.JointX * width - x0) / cropWidth;
            float anchorY = (cut.JointY * height - y0) / cropHeight;
            AppendEntry(entries, cut.Name, cut.JointX, cut.JointY, anchorX, anchorY, first: false);
        }

        // ---- pull-push fill --------------------------------------------------

        private static void PullPush(float[] r, float[] g, float[] b, float[] weight,
                                     float[] hole, Pixels original, int width, int height)
        {
            for (int step = 0; step < InpaintIterations; step++)
            {
                float[] wr = Blur(Multiply(r, weight), width, height);
                float[] wg = Blur(Multiply(g, weight), width, height);
                float[] wb = Blur(Multiply(b, weight), width, height);
                float[] ww = Blur(weight, width, height);

                for (int p = 0; p < hole.Length; p++)
                {
                    if (hole[p] > 0.02f)
                    {
                        float divisor = Mathf.Max(ww[p], 1e-6f);
                        r[p] = wr[p] / divisor;
                        g[p] = wg[p] / divisor;
                        b[p] = wb[p] / divisor;
                        weight[p] = Mathf.Min(ww[p] * 3f, 1f);
                    }
                    else
                    {
                        r[p] = original.R[p];
                        g[p] = original.G[p];
                        b[p] = original.B[p];
                        weight[p] = original.A[p] / 255f;
                    }
                }
            }
        }

        private static void PullPushSingle(float[] values, float[] weight, float[] hole,
                                           float[] original, int width, int height)
        {
            for (int step = 0; step < InpaintIterations; step++)
            {
                float[] weighted = Blur(Multiply(values, weight), width, height);
                float[] blurredWeight = Blur(weight, width, height);

                for (int p = 0; p < hole.Length; p++)
                {
                    if (hole[p] > 0.02f)
                    {
                        values[p] = weighted[p] / Mathf.Max(blurredWeight[p], 1e-6f);
                        weight[p] = Mathf.Min(blurredWeight[p] * 3f, 1f);
                    }
                    else
                    {
                        values[p] = original[p];
                        weight[p] = 1f;
                    }
                }
            }
        }

        private static float[] Multiply(float[] values, float[] factor)
        {
            float[] result = new float[values.Length];
            for (int p = 0; p < values.Length; p++) result[p] = values[p] * factor[p];
            return result;
        }

        /// <summary>A 3x3 box blur that repeats the edge pixel rather than treating outside as zero.</summary>
        private static float[] Blur(float[] values, int width, int height)
        {
            float[] result = new float[values.Length];
            for (int y = 0; y < height; y++)
            {
                for (int x = 0; x < width; x++)
                {
                    float sum = 0f;
                    for (int dy = -1; dy <= 1; dy++)
                    {
                        int sy = Mathf.Clamp(y + dy, 0, height - 1);
                        for (int dx = -1; dx <= 1; dx++)
                        {
                            int sx = Mathf.Clamp(x + dx, 0, width - 1);
                            sum += values[sy * width + sx];
                        }
                    }
                    result[y * width + x] = sum / 9f;
                }
            }
            return result;
        }

        // ---- files -----------------------------------------------------------

        /// <summary>
        /// Channels as top-down float arrays. Unity hands out pixels bottom-up
        /// and the measurements above are top-down; converting once here beats
        /// flipping a y coordinate correctly in nine places.
        /// </summary>
        private readonly struct Pixels
        {
            public readonly float[] R;
            public readonly float[] G;
            public readonly float[] B;
            public readonly float[] A;

            public Pixels(float[] r, float[] g, float[] b, float[] a)
            {
                R = r;
                G = g;
                B = b;
                A = a;
            }

            public static Pixels Empty(int count)
            {
                return new Pixels(new float[count], new float[count], new float[count], new float[count]);
            }

            public static Pixels FromTexture(Texture2D texture)
            {
                int width = texture.width;
                int height = texture.height;
                Color32[] raw = texture.GetPixels32();

                float[] r = new float[width * height];
                float[] g = new float[width * height];
                float[] b = new float[width * height];
                float[] a = new float[width * height];

                for (int y = 0; y < height; y++)
                {
                    int sourceRow = (height - 1 - y) * width;
                    int targetRow = y * width;
                    for (int x = 0; x < width; x++)
                    {
                        Color32 c = raw[sourceRow + x];
                        r[targetRow + x] = c.r;
                        g[targetRow + x] = c.g;
                        b[targetRow + x] = c.b;
                        a[targetRow + x] = c.a;
                    }
                }
                return new Pixels(r, g, b, a);
            }
        }

        private static void WritePng(string assetPath, Pixels pixels, int width, int height,
                                     int cropX, int cropY, int cropWidth, int cropHeight)
        {
            Color32[] output = new Color32[cropWidth * cropHeight];
            for (int y = 0; y < cropHeight; y++)
            {
                int sourceRow = (cropY + y) * width;
                // Back to bottom-up for Unity.
                int targetRow = (cropHeight - 1 - y) * cropWidth;
                for (int x = 0; x < cropWidth; x++)
                {
                    int s = sourceRow + cropX + x;
                    output[targetRow + x] = new Color32(
                        (byte)Mathf.Clamp(Mathf.RoundToInt(pixels.R[s]), 0, 255),
                        (byte)Mathf.Clamp(Mathf.RoundToInt(pixels.G[s]), 0, 255),
                        (byte)Mathf.Clamp(Mathf.RoundToInt(pixels.B[s]), 0, 255),
                        (byte)Mathf.Clamp(Mathf.RoundToInt(pixels.A[s]), 0, 255));
                }
            }

            Texture2D texture = new Texture2D(cropWidth, cropHeight, TextureFormat.RGBA32, false);
            texture.SetPixels32(output);
            texture.Apply(false, false);
            File.WriteAllBytes(EditorPaths.ToAbsolutePath(assetPath), texture.EncodeToPNG());
            Object.DestroyImmediate(texture);

            // Imported here so SharedArtImporter stamps the character's scale
            // on it before anything asks for the Sprite.
            AssetDatabase.ImportAsset(assetPath, ImportAssetOptions.ForceUpdate);
        }

        private static void AppendEntry(StringBuilder builder, string name,
                                        float jointX, float jointY,
                                        float anchorX, float anchorY, bool first)
        {
            if (!first) builder.Append(",\n");
            builder.Append("    {\n");
            builder.Append($"      \"name\": \"{name}\",\n");
            builder.Append($"      \"joint\": {{ \"x\": {Number(jointX)}, \"y\": {Number(jointY)} }},\n");
            builder.Append($"      \"anchor\": {{ \"x\": {Number(anchorX)}, \"y\": {Number(anchorY)} }}\n");
            builder.Append("    }");
        }

        /// <summary>
        /// Invariant culture, always. A machine set to a locale that writes
        /// decimals with a comma would emit "0,197" and produce a rig.json
        /// that no JSON parser will read back.
        /// </summary>
        private static string Number(float value)
        {
            return value.ToString("0.#####", CultureInfo.InvariantCulture);
        }

        private static string BuildManifest(string entries, string sourcePath, string view)
        {
            var builder = new StringBuilder();
            builder.Append("{\n");
            builder.Append("  \"_comment\": \"Character rig for the Runner genre. Cut from ");
            builder.Append(sourcePath);
            builder.Append(" by Assets/GameFactory/Editor/CharacterPartSlicer.cs. joint is where the piece hinges, in fractions of body.png (x from the left, y from the TOP). anchor is where that same point falls inside the piece's own image, same convention, and may sit outside 0-1. Correct a number in the slicer's table and re-slice; do not hand-edit this file.\",\n");
            builder.Append($"  \"source\": \"{SourceName}\",\n");
            builder.Append($"  \"view\": \"{view}\",\n");
            builder.Append($"  \"generated\": \"{System.DateTime.Now:yyyy-MM-dd HH:mm:ss}\",\n");
            builder.Append($"  \"reference\": \"{sourcePath}\",\n");
            builder.Append("  \"parts\": [\n");
            builder.Append(entries);
            builder.Append("\n  ]\n}\n");
            return builder.ToString();
        }
    }
}
