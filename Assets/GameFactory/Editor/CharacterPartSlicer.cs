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
    /// THE NUMBERS ARE MEASURED, NOT GUESSED. They were read off player.png
    /// (136x192): the paws sit at 62-78% of the height, the feet below 87%,
    /// and the hips are placed at 79.5% - up inside the belly, so a leg swings
    /// on an arc instead of pivoting on its own ankle. Different art means
    /// different numbers, and this table is where they change.
    /// </summary>
    public static class CharacterPartSlicer
    {
        public const string SourceSpritePath = "Assets/Common/Art/Runner/player.png";
        public const string RigFolder = "Assets/Common/Art/Runner/rig";
        public const string ManifestPath = RigFolder + "/rig.json";

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

        private static readonly PartCut[] Cuts =
        {
            new PartCut("arm_l", 0.152f, 0.618f, 0.242f, 0.782f, false, 0.197f, 0.612f),
            new PartCut("arm_r", 0.752f, 0.618f, 0.842f, 0.782f, false, 0.797f, 0.612f),
            new PartCut("foot_l", 0.178f, 0.872f, 0.350f, 1.000f, true, 0.264f, 0.795f),
            new PartCut("foot_r", 0.636f, 0.872f, 0.808f, 1.000f, true, 0.722f, 0.795f),
        };

        [MenuItem("Game Factory/Character/Slice player.png into rig parts")]
        private static void SliceFromMenu()
        {
            if (Slice(force: true))
            {
                Debug.Log($"[CharacterPartSlicer] Wrote {Cuts.Length + 1} parts to {RigFolder}.");
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

        public static bool Slice(bool force)
        {
            if (!force && !ShouldReplaceExisting()) return File.Exists(EditorPaths.ToAbsolutePath(ManifestPath));

            string absoluteSource = EditorPaths.ToAbsolutePath(SourceSpritePath);
            if (!File.Exists(absoluteSource))
            {
                // Not an error: a project with no character art keeps the
                // single-sprite player it always had.
                return false;
            }

            // Loaded from the file rather than through the imported Sprite, so
            // the texture is readable whatever Read/Write Enabled is set to on
            // the asset - flipping that flag would be a side effect on a file
            // this generator does not own.
            Texture2D source = new Texture2D(2, 2, TextureFormat.RGBA32, false);
            if (!source.LoadImage(File.ReadAllBytes(absoluteSource)))
            {
                Debug.LogWarning($"[CharacterPartSlicer] {SourceSpritePath} could not be decoded.");
                Object.DestroyImmediate(source);
                return false;
            }

            int width = source.width;
            int height = source.height;
            Pixels image = Pixels.FromTexture(source);
            Object.DestroyImmediate(source);

            float[] combined = new float[width * height];
            float[] rebuildMask = new float[width * height];
            float[][] masks = new float[Cuts.Length][];

            for (int i = 0; i < Cuts.Length; i++)
            {
                masks[i] = BuildSoftMask(Cuts[i], width, height);
                for (int p = 0; p < combined.Length; p++)
                {
                    combined[p] = Mathf.Min(1f, combined[p] + masks[i][p]);
                    if (Cuts[i].RebuildSilhouette)
                    {
                        rebuildMask[p] = Mathf.Min(1f, rebuildMask[p] + masks[i][p]);
                    }
                }
            }

            Directory.CreateDirectory(EditorPaths.ToAbsolutePath(RigFolder));

            WriteBody(image, combined, rebuildMask, width, height);
            var entries = new StringBuilder();
            AppendEntry(entries, "body", 0.5f, 0.5f, 0.5f, 0.5f, first: true);

            for (int i = 0; i < Cuts.Length; i++)
            {
                WritePiece(image, masks[i], Cuts[i], width, height, entries);
            }

            File.WriteAllText(EditorPaths.ToAbsolutePath(ManifestPath),
                              BuildManifest(entries.ToString()), new UTF8Encoding(false));

            AssetDatabase.Refresh();
            return true;
        }

        /// <summary>
        /// True when there is nothing on disk, or what is there was cut by this
        /// tool and can safely be cut again. Art from another source is never
        /// overwritten by an automatic run.
        /// </summary>
        private static bool ShouldReplaceExisting()
        {
            string absolute = EditorPaths.ToAbsolutePath(ManifestPath);
            if (!File.Exists(absolute)) return true;

            string text = File.ReadAllText(absolute);
            return text.Contains($"\"source\": \"{SourceName}\"")
                   || text.Contains($"\"source\": \"{ReplaceableSource}\"");
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

        private static string BuildManifest(string entries)
        {
            var builder = new StringBuilder();
            builder.Append("{\n");
            builder.Append("  \"_comment\": \"Character rig for the Runner genre. Cut from ");
            builder.Append(SourceSpritePath);
            builder.Append(" by Assets/GameFactory/Editor/CharacterPartSlicer.cs. joint is where the piece hinges, in fractions of body.png (x from the left, y from the TOP). anchor is where that same point falls inside the piece's own image, same convention, and may sit outside 0-1. Correct a number in the slicer's table and re-slice; do not hand-edit this file.\",\n");
            builder.Append($"  \"source\": \"{SourceName}\",\n");
            builder.Append($"  \"generated\": \"{System.DateTime.Now:yyyy-MM-dd HH:mm:ss}\",\n");
            builder.Append($"  \"reference\": \"{SourceSpritePath}\",\n");
            builder.Append("  \"parts\": [\n");
            builder.Append(entries);
            builder.Append("\n  ]\n}\n");
            return builder.ToString();
        }
    }
}
