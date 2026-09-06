using UnityEditor;
using UnityEngine;

namespace GameFactory.Editor
{
    /// <summary>
    /// Forces consistent sprite import settings on everything dropped into
    /// Assets/Common/Art/, so licensed art packs can be added by simply
    /// copying files in - no manual Inspector work per texture.
    ///
    /// Full Rect mesh type matters specifically: SpriteRenderer.drawMode
    /// Tiled (used by the ground tile) silently refuses to tile a sprite
    /// imported with the default Tight mesh.
    /// </summary>
    public class SharedArtImporter : AssetPostprocessor
    {
        public const string SharedArtRoot = "Assets/Common/Art/";
        public const string UiArtRoot = "Assets/Common/Art/UI/";

        /// <summary>
        /// The character taken apart: a body with no limbs, and each limb on
        /// its own. Every piece is cut from player.png, so every piece has to
        /// import at player.png's scale and filtering - a paw at the folder
        /// default of 64 would come back twice the size of the body it hangs
        /// off, and the joint offsets in rig.json would all be wrong.
        /// </summary>
        public const string CharacterRigRoot = "Assets/Common/Art/Runner/rig/";

        /// <summary>
        /// 9-slice borders for UI sprites, in pixels, as Unity orders them:
        /// (left, bottom, right, top). Measured from the actual files rather
        /// than guessed - button.png is Kenney's 192x64
        /// button_rectangle_depth_flat, whose colour bands are 4px on the left,
        /// right and top (2px outline + 2px highlight) and 8px on the bottom
        /// (2px highlight + 2px dark + a 4px depth lip). Without a border the
        /// Image stretches all of that, so the lip thickens and the outline
        /// smears as the button gets wider.
        /// </summary>
        private static readonly System.Collections.Generic.Dictionary<string, Vector4> UiBorders =
            new System.Collections.Generic.Dictionary<string, Vector4>
            {
                { "button", new Vector4(4f, 8f, 4f, 4f) },
                { "button_green", new Vector4(4f, 8f, 4f, 4f) },
                { "button_yellow", new Vector4(4f, 8f, 4f, 4f) },
                { "button_red", new Vector4(4f, 8f, 4f, 4f) },
                { "button_grey", new Vector4(4f, 8f, 4f, 4f) },
            };

        /// <summary>
        /// Per-file pixels-per-unit, where the default 64 would be wrong.
        ///
        /// 64 suits the Kenney pack, whose tiles are 70px and chunky. The
        /// player is a background-removed render downscaled to 136x192; at 64
        /// it would be 2.1 x 3.0 world units - three times too tall. At 128 it
        /// is 1.06 x 1.50, a little wider than a 1.09-unit ground tile and half
        /// again as tall, which is the proportion the reference shows.
        /// </summary>
        /// <summary>The character's scale, shared by player.png and every piece cut from it.</summary>
        private const float CharacterPixelsPerUnit = 128f;

        private static readonly System.Collections.Generic.Dictionary<string, float> PixelsPerUnitOverrides =
            new System.Collections.Generic.Dictionary<string, float>
            {
                { "player", CharacterPixelsPerUnit },
            };

        /// <summary>
        /// Files that are smooth artwork rather than pixel art. Point sampling
        /// is right for crisp tile edges but turns a downscaled photoreal
        /// render jagged, so those get bilinear.
        /// </summary>
        private static readonly System.Collections.Generic.HashSet<string> BilinearFiles =
            new System.Collections.Generic.HashSet<string> { "player" };

        private void OnPreprocessTexture()
        {
            if (!assetPath.StartsWith(SharedArtRoot)) return;

            TextureImporter importer = (TextureImporter)assetImporter;

            // Only stamp defaults on first import. Re-stamping on every
            // reimport would silently revert any deliberate per-asset tweak.
            if (!importer.importSettingsMissing) return;

            string fileName = System.IO.Path.GetFileNameWithoutExtension(assetPath);

            bool isCharacterPart = assetPath.StartsWith(CharacterRigRoot);

            float pixelsPerUnit = 64f;
            if (isCharacterPart)
            {
                pixelsPerUnit = CharacterPixelsPerUnit;
            }
            else if (PixelsPerUnitOverrides.TryGetValue(fileName, out float ppu))
            {
                pixelsPerUnit = ppu;
            }

            importer.textureType = TextureImporterType.Sprite;
            importer.spriteImportMode = SpriteImportMode.Single;
            importer.spritePixelsPerUnit = pixelsPerUnit;
            importer.filterMode = isCharacterPart || BilinearFiles.Contains(fileName)
                ? FilterMode.Bilinear
                : FilterMode.Point;
            importer.mipmapEnabled = false;
            importer.alphaIsTransparency = true;
            importer.textureCompression = TextureImporterCompression.Uncompressed;

            TextureImporterSettings settings = new TextureImporterSettings();
            importer.ReadTextureSettings(settings);
            settings.spriteMeshType = SpriteMeshType.FullRect;
            importer.SetTextureSettings(settings);

            if (assetPath.StartsWith(UiArtRoot) && UiBorders.TryGetValue(fileName, out Vector4 border))
            {
                importer.spriteBorder = border;
            }
        }
    }
}
