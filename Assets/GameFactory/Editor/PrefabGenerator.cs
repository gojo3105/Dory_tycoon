using System.IO;
using GameFactory.Core.Spec;
using GameFactory.Gameplay.Runner;
using GameFactory.Modules.GravitySwitch;
using UnityEditor;
using UnityEngine;

namespace GameFactory.Editor
{
    /// <summary>The set of prefab assets a generated Runner game needs to play.</summary>
    public readonly struct RunnerPrefabSet
    {
        public GameObject Player { get; }
        public GameObject GroundTile { get; }
        public GameObject Obstacle { get; }
        /// <summary>The bar that hangs at head height and has to be slid under. Null when the GameSpec has no slide.</summary>
        public GameObject ObstacleOverhead { get; }
        public GameObject Coin { get; }
        /// <summary>Null when the GameSpec does not use the GravitySwitch mechanic.</summary>
        public GameObject GravityZone { get; }
        public int GroundLayer { get; }

        /// <summary>
        /// The player collider's standing height, in world units. The scene
        /// generator needs it to hang the overhead bar: too high and the player
        /// runs under it standing up, too low and no duck fits through.
        /// </summary>
        public float PlayerHeight { get; }

        public RunnerPrefabSet(GameObject player, GameObject groundTile, GameObject obstacle,
            GameObject obstacleOverhead, GameObject coin, GameObject gravityZone,
            int groundLayer, float playerHeight)
        {
            Player = player;
            GroundTile = groundTile;
            Obstacle = obstacle;
            ObstacleOverhead = obstacleOverhead;
            Coin = coin;
            GravityZone = gravityZone;
            GroundLayer = groundLayer;
            PlayerHeight = playerHeight;
        }
    }

    /// <summary>
    /// Creates and saves the placeholder prefabs a Runner game needs: Player,
    /// GroundTile, Obstacle, Coin, and (when used) a GravityZone volume.
    /// Sprites are procedurally generated solid-color placeholders - real art
    /// can replace them later without touching any generation code.
    /// </summary>
    public static class PrefabGenerator
    {
        private const int SpriteSize = 64;

        /// <summary>
        /// Where licensed art packs land. A file here wins over the
        /// procedural placeholder of the same canonical name, so adding real
        /// art is purely additive - if the folder is empty the generator
        /// behaves exactly as before and the verified build cannot break.
        /// </summary>
        private const string SharedArtFolder = "Assets/Common/Art/Runner";

        /// <summary>
        /// How tall the hanging bar is, in world units. Deliberately far taller
        /// than a jump: the camera is 10 units high, the arc of a jump is under
        /// 2, so a bar this size cannot be cleared by going over it. That is
        /// the point - it is the one obstacle the jump does not answer.
        /// </summary>
        public const float OverheadObstacleHeight = 4.5f;

        private const float OverheadObstacleWidth = 1.2f;

        public static RunnerPrefabSet GenerateRunnerPrefabs(GameSpec spec, string assetFolder)
        {
            int groundLayer = TagLayerUtility.EnsureLayer("Ground");
            TagLayerUtility.EnsureTag("Obstacle");

            GameObject player = CreatePlayerPrefab(spec, assetFolder, groundLayer);
            GameObject groundTile = CreateGroundTilePrefab(spec, assetFolder, 10f, groundLayer);
            GameObject obstacle = CreateObstaclePrefab(assetFolder);
            GameObject obstacleOverhead = spec.mechanics.slide ? CreateOverheadObstaclePrefab(assetFolder) : null;
            GameObject coin = CreateCoinPrefab(assetFolder);
            GameObject gravityZone = spec.mechanics.gravitySwitch ? CreateGravityZonePrefab(assetFolder) : null;

            BoxCollider2D playerBox = player != null ? player.GetComponent<BoxCollider2D>() : null;
            float playerHeight = playerBox != null ? playerBox.size.y : 1f;

            return new RunnerPrefabSet(player, groundTile, obstacle, obstacleOverhead, coin,
                gravityZone, groundLayer, playerHeight);
        }

        private static GameObject CreatePlayerPrefab(GameSpec spec, string assetFolder, int groundLayer)
        {
            GameObject go = new GameObject("Player");
            go.tag = "Player";

            Rigidbody2D rb = go.AddComponent<Rigidbody2D>();
            rb.freezeRotation = true;

            // The visual and the collider must occupy the same space. The 3D
            // primitive placeholder put its Body at y=+0.5 and Head at y=+1.05,
            // so the character was drawn ABOVE a 0.9x0.9 box centred on the
            // origin - coins were collected by an invisible box under the
            // character's feet, which is what "the coin pickup position is
            // wrong" was. A centred sprite removes the offset entirely.
            // Priority: a cut-out PNG the user dropped in, then the procedural
            // 도리 built from their reference images, then the 3D primitive.
            // Dropping player.png in later overrides this with no code change,
            // and the generator never writes to that path.
            Sprite playerSprite = FindLicensedSprite("player") ?? DoriSpriteGenerator.EnsureSprite();
            Vector2 bodySize;

            RunnerCharacterMotion motion = null;

            // The visual goes on a CHILD, not the root. RunnerCharacterMotion
            // scales and rotates whatever it is on, and on the root that would
            // scale the BoxCollider2D too - the hitbox would breathe in and
            // out while running.
            GameObject visual = new GameObject("Visual");
            visual.transform.SetParent(go.transform, false);

            // Separated art wins over the single sprite: it is the same
            // drawing, but with paws and feet that can actually move. Absent,
            // GenerateRig returns null and nothing below changes.
            // Cut the parts first if they are not there. Unity does the image
            // work itself, so a plain build produces a character that can move
            // without a key, a service, or anyone running a menu item.
            CharacterPartSlicer.EnsureRigParts();

            CharacterRigGenerator.CharacterRig? rig = CharacterRigGenerator.RigArtExists()
                ? CharacterRigGenerator.GenerateRig(visual.transform, assetFolder)
                : null;

            if (rig.HasValue)
            {
                motion = visual.AddComponent<RunnerCharacterMotion>();
                motion.SetBody(rig.Value.Body);
                motion.SetAnimator(rig.Value.Animator);
                bodySize = rig.Value.BodySize;
            }
            else if (playerSprite != null)
            {
                SpriteRenderer sr = visual.AddComponent<SpriteRenderer>();
                sr.sprite = playerSprite;
                sr.sortingOrder = 10;

                motion = visual.AddComponent<RunnerCharacterMotion>();
                motion.SetBody(sr);
                bodySize = SpriteWorldSize(playerSprite, Vector2.one * 0.9f);
            }
            else
            {
                // No licensed art yet: keep the primitive placeholder rather
                // than shipping an invisible player.
                Object.DestroyImmediate(visual);
                InstantiateMainCharacterVisual(go.transform);
                bodySize = Vector2.one * 0.9f;
            }

            BoxCollider2D col = go.AddComponent<BoxCollider2D>();
            // Slightly narrower than the art so shoulders do not clip obstacles
            // the player visually cleared; full height, so the ground check and
            // the feet agree.
            col.size = new Vector2(bodySize.x * 0.7f, bodySize.y);

            Transform groundCheck = new GameObject("GroundCheck").transform;
            groundCheck.SetParent(go.transform);
            groundCheck.localPosition = new Vector3(0f, -bodySize.y * 0.5f, 0f);

            GameObject magnetGO = new GameObject("CoinMagnet");
            magnetGO.transform.SetParent(go.transform, false);
            magnetGO.AddComponent<CoinMagnet>();

            RunnerPlayerController controller = go.AddComponent<RunnerPlayerController>();
            controller.SetGroundCheck(groundCheck, 1 << groundLayer);
            // Structural wiring at edit time, so the visual never has to search
            // for its controller at runtime.
            if (motion != null) motion.SetController(controller);

            if (spec.mechanics.gravitySwitch) go.AddComponent<GravitySwitchVfx>();

            return SaveAsPrefab(go, assetFolder, "Player.prefab");
        }

        /// <summary>
        /// Attaches the shared MainCharacter(도리) prefab as a purely visual
        /// child - the 2D Rigidbody2D/Collider2D on the Player root above
        /// still drives all movement/collision, per Game01_IMPLEMENTATION_PLAN.md §1.
        /// </summary>
        private static void InstantiateMainCharacterVisual(Transform parent)
        {
            GameObject mainCharacterPrefab = MainCharacterGenerator.EnsureMainCharacterPrefab();
            GameObject instance = (GameObject)PrefabUtility.InstantiatePrefab(mainCharacterPrefab, parent);

            // Instantiating with a parent preserves world position, so pin the
            // visual to the gameplay root explicitly.
            instance.transform.localPosition = Vector3.zero;
            instance.transform.localRotation = Quaternion.identity;
        }

        private static GameObject CreateGroundTilePrefab(GameSpec spec, string assetFolder, float tileWidth, int groundLayer)
        {
            GameObject go = new GameObject("GroundTile");
            go.layer = groundLayer;

            SpriteRenderer sr = go.AddComponent<SpriteRenderer>();
            sr.sprite = ResolveSprite("ground", assetFolder, "ground_sprite.png", MutedColorFromSeed(spec.theme.environment));
            sr.drawMode = SpriteDrawMode.Tiled;
            sr.size = new Vector2(tileWidth, 1f);

            BoxCollider2D col = go.AddComponent<BoxCollider2D>();
            col.size = new Vector2(tileWidth, 1f);

            return SaveAsPrefab(go, assetFolder, "GroundTile.prefab");
        }

        private static GameObject CreateObstaclePrefab(string assetFolder)
        {
            GameObject go = new GameObject("Obstacle");
            go.tag = "Obstacle";

            SpriteRenderer sr = go.AddComponent<SpriteRenderer>();
            sr.sprite = ResolveSprite("obstacle", assetFolder, "obstacle_sprite.png", new Color(0.85f, 0.15f, 0.15f));

            BoxCollider2D col = go.AddComponent<BoxCollider2D>();
            col.isTrigger = true;
            col.size = SpriteWorldSize(sr.sprite, Vector2.one);

            return SaveAsPrefab(go, assetFolder, "Obstacle.prefab");
        }

        /// <summary>
        /// The bar that hangs at head height. Same "Obstacle" tag as the ground
        /// one, so it kills on contact through exactly the same code path - the
        /// only difference is where the spawner hangs it and which verb clears it.
        ///
        /// Drawn tiled rather than stretched, the way the ground tile is: a
        /// 64px sprite scaled to 4.5 units tall would be a blurred smear, and
        /// tiling keeps whatever texture real art brings at its own size.
        /// </summary>
        private static GameObject CreateOverheadObstaclePrefab(string assetFolder)
        {
            GameObject go = new GameObject("ObstacleOverhead");
            go.tag = "Obstacle";

            Vector2 size = new Vector2(OverheadObstacleWidth, OverheadObstacleHeight);

            SpriteRenderer sr = go.AddComponent<SpriteRenderer>();
            sr.sprite = ResolveSprite("obstacle_overhead", assetFolder, "obstacle_overhead_sprite.png",
                new Color(0.72f, 0.36f, 0.12f));
            sr.drawMode = SpriteDrawMode.Tiled;
            sr.size = size;

            BoxCollider2D col = go.AddComponent<BoxCollider2D>();
            col.isTrigger = true;
            col.size = size;

            return SaveAsPrefab(go, assetFolder, "ObstacleOverhead.prefab");
        }

        private static GameObject CreateCoinPrefab(string assetFolder)
        {
            GameObject go = new GameObject("Coin");

            SpriteRenderer sr = go.AddComponent<SpriteRenderer>();
            sr.sprite = ResolveSprite("coin", assetFolder, "coin_sprite.png", new Color(1f, 0.85f, 0.2f), 48);

            BoxCollider2D col = go.AddComponent<BoxCollider2D>();
            col.isTrigger = true;
            // 0.6 was too tight: Kenney's coin fills most of its frame, so a
            // trigger at 60% of the art meant grazing a coin and not getting
            // it. In a runner the player has no time to aim, so pickup should
            // be forgiving - just inside the art, not well inside it.
            col.size = SpriteWorldSize(sr.sprite, Vector2.one) * 0.9f;

            go.AddComponent<Coin>();

            return SaveAsPrefab(go, assetFolder, "Coin.prefab");
        }

        private static GameObject CreateGravityZonePrefab(string assetFolder)
        {
            GameObject go = new GameObject("GravityZone");

            BoxCollider2D col = go.AddComponent<BoxCollider2D>();
            col.isTrigger = true;
            col.size = new Vector2(10f, 6f);

            SpriteRenderer sr = go.AddComponent<SpriteRenderer>();
            sr.sprite = ResolveSprite("gravity_zone", assetFolder, "gravity_zone_sprite.png", new Color(0.55f, 0.2f, 0.9f, 0.25f));
            sr.drawMode = SpriteDrawMode.Tiled;
            sr.size = new Vector2(10f, 6f);
            sr.sortingOrder = -1;

            go.AddComponent<GravitySwitchZone>();

            return SaveAsPrefab(go, assetFolder, "GravityZone.prefab");
        }

        /// <summary>
        /// Returns licensed art from SharedArtFolder when present, otherwise
        /// generates the solid-colour placeholder. Callers get a Sprite either
        /// way and do not need to care which they got.
        /// </summary>
        /// <summary>
        /// The licensed sprite for this name, or null. Separate from
        /// ResolveSprite because the player needs to know whether real art
        /// exists before deciding how to build itself, and must not have a
        /// solid-colour placeholder generated as a side effect of asking.
        /// </summary>
        private static Sprite FindLicensedSprite(string canonicalName)
        {
            return AssetDatabase.LoadAssetAtPath<Sprite>($"{SharedArtFolder}/{canonicalName}.png");
        }

        private static Sprite ResolveSprite(string canonicalName, string assetFolder, string placeholderFileName,
            Color placeholderColor, int placeholderSize = SpriteSize)
        {
            Sprite licensed = FindLicensedSprite(canonicalName);
            if (licensed != null) return licensed;

            string placeholderPath = CreateSolidSprite(assetFolder, placeholderFileName, placeholderColor, placeholderSize);
            return AssetDatabase.LoadAssetAtPath<Sprite>(placeholderPath);
        }

        /// <summary>
        /// Sizes a collider from the sprite it is paired with. Real art can be
        /// any resolution, so a hardcoded collider size that matched the 64px
        /// placeholder would no longer line up with what the player sees.
        /// </summary>
        private static Vector2 SpriteWorldSize(Sprite sprite, Vector2 fallback)
        {
            if (sprite == null) return fallback;
            return sprite.bounds.size;
        }

        private static Color MutedColorFromSeed(string seed)
        {
            float hue = Mathf.Abs(SeedHash(seed + "_ground")) % 360 / 360f;
            return Color.HSVToRGB(hue, 0.25f, 0.4f);
        }

        private static int SeedHash(string seed)
        {
            return string.IsNullOrEmpty(seed) ? 0 : seed.GetHashCode();
        }

        private static string CreateSolidSprite(string assetFolder, string fileName, Color color, int size = SpriteSize)
        {
            string assetPath = $"{assetFolder}/{fileName}";
            Directory.CreateDirectory(EditorPaths.ToAbsolutePath(assetFolder));

            Texture2D texture = new Texture2D(size, size, TextureFormat.RGBA32, false);
            Color32[] pixels = new Color32[size * size];
            Color32 pixelColor = color;
            for (int i = 0; i < pixels.Length; i++) pixels[i] = pixelColor;
            texture.SetPixels32(pixels);
            texture.Apply();

            File.WriteAllBytes(EditorPaths.ToAbsolutePath(assetPath), texture.EncodeToPNG());
            Object.DestroyImmediate(texture);

            AssetDatabase.ImportAsset(assetPath);
            TextureImporter importer = (TextureImporter)AssetImporter.GetAtPath(assetPath);
            importer.textureType = TextureImporterType.Sprite;
            importer.spriteImportMode = SpriteImportMode.Single;
            importer.spritePixelsPerUnit = size;
            importer.filterMode = FilterMode.Point;
            importer.mipmapEnabled = false;
            importer.alphaIsTransparency = true;
            importer.SaveAndReimport();

            return assetPath;
        }

        private static GameObject SaveAsPrefab(GameObject sceneInstance, string assetFolder, string fileName)
        {
            Directory.CreateDirectory(EditorPaths.ToAbsolutePath(assetFolder));
            string assetPath = $"{assetFolder}/{fileName}";
            GameObject prefab = PrefabUtility.SaveAsPrefabAsset(sceneInstance, assetPath);
            Object.DestroyImmediate(sceneInstance);
            return prefab;
        }
    }
}
