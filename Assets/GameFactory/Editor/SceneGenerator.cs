using System.IO;
using GameFactory.Core;
using GameFactory.Core.Spec;
using GameFactory.Gameplay.Runner;
using GameFactory.UI;
using UnityEditor;
using UnityEditor.SceneManagement;
using UnityEngine;
using UnityEngine.EventSystems;
using UnityEngine.SceneManagement;
using UnityEngine.UI;

namespace GameFactory.Editor
{
    /// <summary>
    /// Assembles and saves the .unity scene for a generated Runner game:
    /// player, camera, spawners, gimmick level content, UI, and the scene's
    /// single GameManager. Structural wiring only - GameSpec-driven numeric
    /// tuning happens at runtime via RunnerGameInitializer.
    ///
    /// The UI follows the casual direction the design settled on: a bright sky,
    /// cream cards with a thick brown outline, one green primary action per
    /// screen, and Korean labels throughout.
    /// </summary>
    public static class SceneGenerator
    {
        private const string ButtonSpritePath = "Assets/Common/Art/UI/button.png";
        private const string ButtonGreenPath = "Assets/Common/Art/UI/button_green.png";
        private const string ButtonYellowPath = "Assets/Common/Art/UI/button_yellow.png";
        private const string ButtonRedPath = "Assets/Common/Art/UI/button_red.png";
        private const string ButtonGreyPath = "Assets/Common/Art/UI/button_grey.png";
        private const string CoinSpritePath = "Assets/Common/Art/Runner/coin.png";
        private const string PlayerSpritePath = "Assets/Common/Art/Runner/player.png";

        private const float GroundY = -1f;
        private const float ObstacleY = 0f;
        private const float CoinY = 1f;
        private const float TileWidth = 10f;

        /// <summary>Top surface of the ground: the tile is 1 unit tall and centred on GroundY.</summary>
        private const float GroundSurfaceY = GroundY + 0.5f;

        /// <summary>
        /// Where the underside of a hanging bar sits, as a fraction of the
        /// player's standing height. The slide halves the collider, so this
        /// leaves a sixth of a body height to duck through - enough that the
        /// visual squash, which eases in over about a tenth of a second, has
        /// finished before it matters - while still being a third of a body
        /// below a standing head. There is no walking under it upright.
        /// Player art is 1.5 units tall, so the gap is 0.24 units.
        /// </summary>
        private const float OverheadClearanceFraction = 0.66f;

        /// <summary>One margin for the whole UI. The old HUD indented the score to x=180 with no matching margin anywhere else on screen.</summary>
        private const float Margin = 44f;

        /// <summary>Reference canvas width, so full-width controls can be sized once.</summary>
        private const float ContentWidth = 720f - (Margin * 2f);

        // The palette, kept in sync with UiSpriteGenerator - these are the text
        // colours, that file draws the panels they sit on.
        private static readonly Color Ink = new Color32(0x5B, 0x3A, 0x22, 0xFF);
        private static readonly Color DarkText = new Color32(0x4A, 0x2E, 0x18, 0xFF);
        private static readonly Color MutedText = new Color32(0x8A, 0x6A, 0x4A, 0xFF);
        private static readonly Color OrangeText = new Color32(0xE0, 0x82, 0x1C, 0xFF);
        private static readonly Color BrownText = new Color32(0x6B, 0x4A, 0x2F, 0xFF);
        private static readonly Color SkyMid = new Color32(0xA8, 0xE4, 0xFA, 0xFF);
        private static readonly Color Scrim = new Color(0.149f, 0.086f, 0.039f, 0.45f);

        private static Font cachedFont;

        public static string GenerateRunnerScene(GameSpec spec, RunnerPrefabSet prefabs, string sceneFolder)
        {
            UiSpriteGenerator.EnsureSprites();

            Scene scene = EditorSceneManager.NewScene(NewSceneSetup.EmptyScene, NewSceneMode.Single);

            GameObject playerInstance = (GameObject)PrefabUtility.InstantiatePrefab(prefabs.Player, scene);
            playerInstance.transform.position = Vector3.zero;
            RunnerPlayerController playerController = playerInstance.GetComponent<RunnerPlayerController>();

            GameObject gameManagerGO = new GameObject("GameManager");
            GameManager gameManager = gameManagerGO.AddComponent<GameManager>();
            gameManager.SetGameId(spec.game.id);

            new GameObject("TapInput").AddComponent<TapInput>();

            GameObject audioManagerGO = new GameObject("AudioManager");
            audioManagerGO.AddComponent<AudioSource>();
            audioManagerGO.AddComponent<AudioManager>();

            CreateVfxManager();

            GameObject cameraGO = new GameObject("Main Camera");
            cameraGO.tag = "MainCamera";
            cameraGO.transform.position = new Vector3(2f, 0f, -10f);
            Camera cam = cameraGO.AddComponent<Camera>();
            cam.orthographic = true;
            cam.orthographicSize = 5f;
            cam.clearFlags = CameraClearFlags.SolidColor;
            // Sky blue, not the near-black it used to be. The gradient sprite
            // below covers the frustum, so this only shows on an aspect ratio
            // wider than the quad - matching it keeps that invisible.
            cam.backgroundColor = SkyMid;
            CameraFollow2D follow = cameraGO.AddComponent<CameraFollow2D>();
            follow.SetTarget(playerInstance.transform);

            CreateSky(cameraGO.transform);

            GameObject groundSpawnerGO = new GameObject("GroundSpawner");
            GroundSpawner groundSpawner = groundSpawnerGO.AddComponent<GroundSpawner>();
            groundSpawner.SetReferences(prefabs.GroundTile, playerInstance.transform, TileWidth, GroundY);

            GameObject obstacleSpawnerGO = new GameObject("ObstacleSpawner");
            ObstacleSpawner obstacleSpawner = obstacleSpawnerGO.AddComponent<ObstacleSpawner>();
            obstacleSpawner.SetReferences(prefabs.Obstacle, playerInstance.transform, ObstacleY);
            if (prefabs.ObstacleOverhead != null)
            {
                obstacleSpawner.SetOverheadReferences(
                    prefabs.ObstacleOverhead, OverheadBarCentreY(prefabs.PlayerHeight));
            }

            GameObject coinSpawnerGO = new GameObject("CoinSpawner");
            CoinSpawner coinSpawner = coinSpawnerGO.AddComponent<CoinSpawner>();
            coinSpawner.SetReferences(prefabs.Coin, playerInstance.transform, CoinY);

            GameObject distanceGO = new GameObject("RunnerDistanceTracker");
            RunnerDistanceTracker distanceTracker = distanceGO.AddComponent<RunnerDistanceTracker>();
            distanceTracker.SetTarget(playerInstance.transform);

            GameObject initializerGO = new GameObject("RunnerGameInitializer");
            RunnerGameInitializer initializer = initializerGO.AddComponent<RunnerGameInitializer>();
            initializer.SetTargets(playerController, obstacleSpawner, coinSpawner);

            LevelGenerator.ConfigureRunnerLevel(spec, playerInstance.transform, prefabs.GravityZone);

            BuildUI(spec.game.title);
            EnsureEventSystem(scene);

            Directory.CreateDirectory(EditorPaths.ToAbsolutePath(sceneFolder));
            string sceneAssetPath = $"{sceneFolder}/{spec.game.id}.unity";
            EditorSceneManager.SaveScene(scene, sceneAssetPath);
            AddSceneToBuildSettings(sceneAssetPath);

            return sceneAssetPath;
        }

        /// <summary>
        /// Centre of a hanging bar, from the player's standing height. The
        /// spawner positions prefabs by their centre and the bar's collider is
        /// centred on it, so the underside is half a bar-height lower - which
        /// is the number that actually decides whether a duck fits.
        /// </summary>
        private static float OverheadBarCentreY(float playerHeight)
        {
            float underside = GroundSurfaceY + playerHeight * OverheadClearanceFraction;
            return underside + PrefabGenerator.OverheadObstacleHeight * 0.5f;
        }

        /// <summary>
        /// The sky gradient, parented to the camera so it needs no follow
        /// script: an orthographic camera's frustum is a fixed size, so a quad
        /// sized once in camera space covers it forever.
        /// </summary>
        private static void CreateSky(Transform cameraTransform)
        {
            Sprite sky = AssetDatabase.LoadAssetAtPath<Sprite>(UiSpriteGenerator.SkyPath);
            if (sky == null) return;

            GameObject skyGO = new GameObject("Sky");
            skyGO.transform.SetParent(cameraTransform, false);
            // +20 in front of the camera, well behind everything at z=0, and
            // inside the default 1000-unit far plane.
            skyGO.transform.localPosition = new Vector3(0f, 0f, 20f);
            // The sprite is 1x4 world units at PPU 64; 20 x 3 makes it 20x12,
            // which covers the 5.6x10 frustum with room for wider screens.
            skyGO.transform.localScale = new Vector3(20f, 3f, 1f);

            SpriteRenderer renderer = skyGO.AddComponent<SpriteRenderer>();
            renderer.sprite = sky;
            renderer.sortingOrder = -100;
        }

        private static void BuildUI(string gameTitle)
        {
            GameObject canvasGO = new GameObject("Canvas");
            Canvas canvas = canvasGO.AddComponent<Canvas>();
            canvas.renderMode = RenderMode.ScreenSpaceOverlay;

            CanvasScaler scaler = canvasGO.AddComponent<CanvasScaler>();
            scaler.uiScaleMode = CanvasScaler.ScaleMode.ScaleWithScreenSize;
            scaler.referenceResolution = new Vector2(720f, 1280f);
            scaler.matchWidthOrHeight = 0.5f;

            canvasGO.AddComponent<GraphicRaycaster>();

            // Every label is Korean, and the built-in font Unity hands out is
            // Latin-only. This swaps them all onto an OS font with Hangul at
            // startup - it cannot be done here, since a runtime-created font
            // does not serialise into the saved scene.
            canvasGO.AddComponent<KoreanFontApplier>();

            GameObject safeAreaGO = new GameObject("SafeArea", typeof(RectTransform));
            safeAreaGO.transform.SetParent(canvasGO.transform, false);
            RectTransform safeAreaRect = safeAreaGO.GetComponent<RectTransform>();
            safeAreaRect.anchorMin = Vector2.zero;
            safeAreaRect.anchorMax = Vector2.one;
            safeAreaRect.sizeDelta = Vector2.zero;
            safeAreaRect.anchoredPosition = Vector2.zero;
            safeAreaGO.AddComponent<SafeAreaFitter>();

            Transform root = safeAreaGO.transform;

            (GameObject hudRoot, Text scoreText, Text hudCoinText, Button pauseButton) = BuildHud(root);
            (GameObject gameOverPanel, Text finalScoreText, Text bestScoreText, Text runCoinsText,
                GameObject newBestBadge, Button restartButton, Button homeButton, Button gameOverShopButton)
                = BuildGameOverUI(root);
            (GameObject pausePanel, Button resumeButton, Button pauseHomeButton) = BuildPauseUI(root);

            // Panels are added in draw order: the shop sits above game-over, and
            // the title above both, because a Canvas child drawn later wins.
            (GameObject titlePanel, Text titleBestText, Text titleCurrencyText,
                Button playButton, Button titleShopButton) = BuildTitleUI(root, gameTitle);

            BuildShopUI(root, new[] { titleShopButton, gameOverShopButton });

            // Button clicks (Restart/Home/Play/Shop/Pause) are all wired at
            // runtime by GameUIController/ShopController, not here:
            // onClick.AddListener registers a non-persistent listener, which is
            // not serialized into the saved scene, so wiring it at edit time
            // would silently produce dead buttons.
            GameObject controllerGO = new GameObject("GameUIController");
            GameUIController controller = controllerGO.AddComponent<GameUIController>();
            controller.SetHudReferences(hudRoot, scoreText, hudCoinText, pauseButton);
            controller.SetGameOverReferences(gameOverPanel, finalScoreText, bestScoreText,
                runCoinsText, newBestBadge, restartButton, homeButton);
            controller.SetPauseReferences(pausePanel, resumeButton, pauseHomeButton);
            controller.SetTitleReferences(titlePanel, titleBestText, titleCurrencyText, playButton);
        }

        // ---- HUD -------------------------------------------------------------

        /// <summary>
        /// Distance top-left, coins and pause top-right, one margin all round.
        /// The in-run coin counter is new: currency lived only in the shop
        /// panel, so the pickup the whole economy rests on gave no feedback
        /// while playing.
        /// </summary>
        private static (GameObject root, Text score, Text coins, Button pause) BuildHud(Transform parent)
        {
            GameObject hud = CreateRect(parent, "HUD", Vector2.zero, Vector2.one, new Vector2(0.5f, 0.5f),
                Vector2.zero, Vector2.zero);

            Text score = CreateText(hud.transform, "ScoreText", "0", 92, TextAnchor.UpperLeft,
                new Vector2(0f, 1f), new Vector2(0f, 1f), new Vector2(0f, 1f),
                new Vector2(420f, 110f), new Vector2(Margin, -56f));
            AddOutline(score, 3f);

            Text unit = CreateText(hud.transform, "ScoreUnitText", "m", 30, TextAnchor.UpperLeft,
                new Vector2(0f, 1f), new Vector2(0f, 1f), new Vector2(0f, 1f),
                new Vector2(120f, 40f), new Vector2(Margin + 4f, -158f));
            AddOutline(unit, 2f);

            Button pause = CreateIconButton(hud.transform, "PauseButton", ButtonGreyPath,
                new Vector2(82f, 82f), new Vector2(1f, 1f), new Vector2(-Margin, -56f));
            Color pauseInk = new Color32(0x4A, 0x4A, 0x4A, 0xFF);
            CreateBar(pause.transform, "BarLeft", new Vector2(10f, 34f), new Vector2(-9f, 0f), 0f, pauseInk);
            CreateBar(pause.transform, "BarRight", new Vector2(10f, 34f), new Vector2(9f, 0f), 0f, pauseInk);

            // Left of the pause button, with the same 14px gap the design uses
            // between adjacent controls.
            Text coins = CreatePill(hud.transform, "HudCoinPill", new Vector2(180f, 76f),
                new Vector2(1f, 1f), new Vector2(-(Margin + 82f + 14f), -56f), "0", 36, OrangeText);

            return (hud, score, coins, pause);
        }

        // ---- title -----------------------------------------------------------

        /// <summary>
        /// Title screen: game name, best distance, coin balance, and the two
        /// things a player can do. Its background is transparent on purpose -
        /// the level is already drawn behind it, and the old opaque near-black
        /// panel hid the character the screen is meant to sell.
        /// </summary>
        private static (GameObject panel, Text bestText, Text currencyText, Button play, Button shop)
            BuildTitleUI(Transform parent, string gameTitle)
        {
            GameObject titlePanel = CreateFullScreenPanel(parent, "TitlePanel", new Color(0f, 0f, 0f, 0f));

            CanvasGroup titleCanvasGroup = titlePanel.AddComponent<CanvasGroup>();
            titleCanvasGroup.alpha = 1f;
            titlePanel.AddComponent<PanelTransition>();

            // Two weights, one arc of emphasis. The spec title is the smaller
            // line so a longer game name cannot crowd the screen.
            Text titleTop = CreateText(titlePanel.transform, "GameTitleText", gameTitle, 56, TextAnchor.MiddleCenter,
                new Vector2(0.5f, 1f), new Vector2(0.5f, 1f), new Vector2(0.5f, 1f),
                new Vector2(640f, 80f), new Vector2(0f, -150f));
            AddOutline(titleTop, 3f);

            Text titleBottom = CreateText(titlePanel.transform, "GameSubtitleText", "러너", 104, TextAnchor.MiddleCenter,
                new Vector2(0.5f, 1f), new Vector2(0.5f, 1f), new Vector2(0.5f, 1f),
                new Vector2(640f, 130f), new Vector2(0f, -232f));
            AddOutline(titleBottom, 5f);

            Text currencyText = CreatePill(titlePanel.transform, "TitleCoinPill", new Vector2(200f, 76f),
                new Vector2(1f, 1f), new Vector2(-Margin, -56f), "0", 38, OrangeText);

            // The controls, spelled out. A verb nobody knows about is a verb
            // the game does not have: the slide is invisible until someone
            // happens to drag downward, and nothing on screen would ever
            // suggest doing that. Two short lines, above the best-score sign.
            Text controlsTop = CreateText(titlePanel.transform, "ControlsHintTop",
                "탭 = 점프 · 공중에서 한 번 더 = 이단 점프", 26, TextAnchor.MiddleCenter,
                new Vector2(0.5f, 0f), new Vector2(0.5f, 0f), new Vector2(0.5f, 0f),
                new Vector2(680f, 40f), new Vector2(0f, 484f));
            AddOutline(controlsTop, 2f);

            Text controlsBottom = CreateText(titlePanel.transform, "ControlsHintBottom",
                "아래로 밀기 = 슬라이드", 26, TextAnchor.MiddleCenter,
                new Vector2(0.5f, 0f), new Vector2(0.5f, 0f), new Vector2(0.5f, 0f),
                new Vector2(680f, 40f), new Vector2(0f, 446f));
            AddOutline(controlsBottom, 2f);

            // Best score on a sign rather than floating text.
            GameObject bestSign = CreatePanel(titlePanel.transform, "BestSign", UiSpriteGenerator.CreamPanelPath,
                new Vector2(440f, 76f), new Vector2(0.5f, 0f), new Vector2(0.5f, 0f), new Vector2(0f, 340f));
            CreateText(bestSign.transform, "Caption", "최고 기록", 26, TextAnchor.MiddleLeft,
                new Vector2(0f, 0.5f), new Vector2(0f, 0.5f), new Vector2(0f, 0.5f),
                new Vector2(180f, 50f), new Vector2(34f, 0f)).color = MutedText;
            Text bestText = CreateText(bestSign.transform, "Value", "0", 40, TextAnchor.MiddleRight,
                new Vector2(1f, 0.5f), new Vector2(1f, 0.5f), new Vector2(1f, 0.5f),
                new Vector2(200f, 50f), new Vector2(-34f, 0f));
            bestText.color = OrangeText;

            // Vertical budget measured from the bottom edge so nothing overlaps:
            // shop 52..158 | play 176..318 | best sign 340..416 |
            // controls hint 426..504.
            (Button play, Text playLabel) = CreateButton(titlePanel.transform, "PlayButton", ButtonGreenPath,
                new Vector2(ContentWidth, 142f), new Vector2(0.5f, 0f), new Vector2(0f, 176f),
                "시작", 62);
            AddOutline(playLabel, 3f);

            (Button shop, Text shopLabel) = CreateButton(titlePanel.transform, "TitleShopButton", ButtonYellowPath,
                new Vector2(ContentWidth, 106f), new Vector2(0.5f, 0f), new Vector2(0f, 52f),
                "상점", 40);
            AddOutline(shopLabel, 3f);

            return (titlePanel, bestText, currencyText, play, shop);
        }

        // ---- game over -------------------------------------------------------

        /// <summary>
        /// One card, then one primary action. The old panel stacked Restart,
        /// Home and Shop as three near-identical bars, so the button wanted
        /// almost every time competed with the two that are not.
        /// </summary>
        private static (GameObject panel, Text finalScore, Text bestScore, Text runCoins,
            GameObject newBestBadge, Button restart, Button home, Button shop) BuildGameOverUI(Transform parent)
        {
            GameObject panel = CreateFullScreenPanel(parent, "GameOverPanel", Scrim);

            GameObject card = CreatePanel(panel.transform, "Card", UiSpriteGenerator.CreamPanelPath,
                new Vector2(640f, 600f), new Vector2(0.5f, 1f), new Vector2(0.5f, 1f), new Vector2(0f, -172f));

            GameObject badge = CreatePanel(card.transform, "NewBestBadge", UiSpriteGenerator.GoldPanelPath,
                new Vector2(260f, 72f), new Vector2(0.5f, 1f), new Vector2(0.5f, 1f), new Vector2(0f, -22f));
            Text badgeLabel = CreateText(badge.transform, "Label", "신기록!", 34, TextAnchor.MiddleCenter,
                Vector2.zero, Vector2.one, new Vector2(0.5f, 0.5f), Vector2.zero, Vector2.zero);
            AddOutline(badgeLabel, 3f);
            badge.SetActive(false);

            CreateText(card.transform, "RunCaption", "이번 기록", 28, TextAnchor.MiddleCenter,
                new Vector2(0.5f, 1f), new Vector2(0.5f, 1f), new Vector2(0.5f, 1f),
                new Vector2(400f, 44f), new Vector2(0f, -112f)).color = MutedText;

            Text finalScore = CreateText(card.transform, "FinalScoreText", "0", 120, TextAnchor.MiddleCenter,
                new Vector2(0.5f, 1f), new Vector2(0.5f, 1f), new Vector2(0.5f, 1f),
                new Vector2(600f, 150f), new Vector2(0f, -158f));
            AddOutline(finalScore, 5f);

            CreateText(card.transform, "RunUnit", "미터", 28, TextAnchor.MiddleCenter,
                new Vector2(0.5f, 1f), new Vector2(0.5f, 1f), new Vector2(0.5f, 1f),
                new Vector2(200f, 40f), new Vector2(0f, -312f)).color = MutedText;

            GameObject divider = CreateRect(card.transform, "Divider",
                new Vector2(0.5f, 1f), new Vector2(0.5f, 1f), new Vector2(0.5f, 1f),
                new Vector2(560f, 5f), new Vector2(0f, -358f));
            divider.AddComponent<Image>().color = new Color32(0xE8, 0xD8, 0xBE, 0xFF);

            // Two facts, not a statistics panel.
            Text runCoins = CreateStatBox(card.transform, "RunCoinsBox", new Vector2(-145f, -382f),
                "+0", OrangeText, "이번 판 코인", coinIcon: true);
            Text bestScore = CreateStatBox(card.transform, "PreviousBestBox", new Vector2(145f, -382f),
                "0", BrownText, "이전 최고", coinIcon: false);

            (Button restart, Text restartLabel) = CreateButton(panel.transform, "RestartButton", ButtonGreenPath,
                new Vector2(ContentWidth, 142f), new Vector2(0.5f, 0f), new Vector2(0f, 176f),
                "다시하기", 56);
            AddOutline(restartLabel, 3f);

            // Two secondaries side by side: 632 total, an 18px gap, 307 each.
            (Button home, Text homeLabel) = CreateButton(panel.transform, "HomeButton", ButtonGreyPath,
                new Vector2(307f, 106f), new Vector2(0.5f, 0f), new Vector2(-162.5f, 52f), "홈", 40);
            AddOutline(homeLabel, 3f);

            (Button shop, Text shopLabel) = CreateButton(panel.transform, "ShopButton", ButtonYellowPath,
                new Vector2(307f, 106f), new Vector2(0.5f, 0f), new Vector2(162.5f, 52f), "상점", 40);
            AddOutline(shopLabel, 3f);

            CanvasGroup panelCanvasGroup = panel.AddComponent<CanvasGroup>();
            panelCanvasGroup.alpha = 0f;
            panel.AddComponent<PanelTransition>();
            panel.SetActive(false);

            return (panel, finalScore, bestScore, runCoins, badge, restart, home, shop);
        }

        /// <summary>One of the two figures under the game-over score. Returns the value label.</summary>
        private static Text CreateStatBox(Transform parent, string name, Vector2 anchoredPosition,
            string value, Color valueColor, string caption, bool coinIcon)
        {
            GameObject box = CreatePanel(parent, name, UiSpriteGenerator.DimPanelPath,
                new Vector2(270f, 140f), new Vector2(0.5f, 1f), new Vector2(0.5f, 1f), anchoredPosition);

            // With the coin icon the value shifts right to make room for it, so
            // icon and number read as one unit instead of two centred things.
            float valueOffset = coinIcon ? 22f : 0f;
            if (coinIcon)
            {
                CreateSpriteImage(box.transform, "CoinIcon", CoinSpritePath, new Vector2(36f, 36f),
                    new Vector2(0.5f, 1f), new Vector2(-72f, -44f));
            }

            Text valueText = CreateText(box.transform, "Value", value, 46, TextAnchor.MiddleCenter,
                new Vector2(0.5f, 1f), new Vector2(0.5f, 1f), new Vector2(0.5f, 1f),
                new Vector2(200f, 60f), new Vector2(valueOffset, -44f));
            valueText.color = valueColor;

            CreateText(box.transform, "Caption", caption, 24, TextAnchor.MiddleCenter,
                new Vector2(0.5f, 1f), new Vector2(0.5f, 1f), new Vector2(0.5f, 1f),
                new Vector2(250f, 40f), new Vector2(0f, -100f)).color = MutedText;

            return valueText;
        }

        // ---- pause -----------------------------------------------------------

        /// <summary>
        /// A run could not be interrupted at all before this. Nothing here is
        /// on a Time.timeScale-driven animation - PanelTransition already fades
        /// on unscaled time, which is what lets this show while the game is frozen.
        /// </summary>
        private static (GameObject panel, Button resume, Button home) BuildPauseUI(Transform parent)
        {
            GameObject panel = CreateFullScreenPanel(parent, "PausePanel", Scrim);

            GameObject card = CreatePanel(panel.transform, "Card", UiSpriteGenerator.CreamPanelPath,
                new Vector2(560f, 420f), new Vector2(0.5f, 0.5f), new Vector2(0.5f, 1f), new Vector2(0f, 210f));

            CreateText(card.transform, "Title", "일시정지", 52, TextAnchor.MiddleCenter,
                new Vector2(0.5f, 1f), new Vector2(0.5f, 1f), new Vector2(0.5f, 1f),
                new Vector2(480f, 80f), new Vector2(0f, -46f)).color = DarkText;

            (Button resume, Text resumeLabel) = CreateButton(card.transform, "ResumeButton", ButtonGreenPath,
                new Vector2(480f, 120f), new Vector2(0.5f, 1f), new Vector2(0f, -150f), "계속하기", 44);
            AddOutline(resumeLabel, 3f);

            (Button home, Text homeLabel) = CreateButton(card.transform, "PauseHomeButton", ButtonGreyPath,
                new Vector2(480f, 100f), new Vector2(0.5f, 1f), new Vector2(0f, -286f), "홈", 38);
            AddOutline(homeLabel, 3f);

            CanvasGroup canvasGroup = panel.AddComponent<CanvasGroup>();
            canvasGroup.alpha = 0f;
            panel.AddComponent<PanelTransition>();
            panel.SetActive(false);

            return (panel, resume, home);
        }

        // ---- shop ------------------------------------------------------------

        private static void BuildShopUI(Transform parent, Button[] openButtons)
        {
            GameObject shopPanel = CreateFullScreenPanel(parent, "ShopPanel", SkyMid);

            GameObject ribbon = CreatePanel(shopPanel.transform, "TitleRibbon", UiSpriteGenerator.GoldPanelPath,
                new Vector2(200f, 88f), new Vector2(0f, 1f), new Vector2(0f, 1f), new Vector2(Margin, -52f));
            Text ribbonLabel = CreateText(ribbon.transform, "Label", "상점", 48, TextAnchor.MiddleCenter,
                Vector2.zero, Vector2.one, new Vector2(0.5f, 0.5f), Vector2.zero, Vector2.zero);
            AddOutline(ribbonLabel, 3f);

            Button closeButton = CreateIconButton(shopPanel.transform, "CloseButton", ButtonRedPath,
                new Vector2(82f, 82f), new Vector2(1f, 1f), new Vector2(-Margin, -56f));
            CreateBar(closeButton.transform, "BarA", new Vector2(9f, 40f), Vector2.zero, 45f, Color.white);
            CreateBar(closeButton.transform, "BarB", new Vector2(9f, 40f), Vector2.zero, -45f, Color.white);

            Text currencyText = CreatePill(shopPanel.transform, "ShopCoinPill", new Vector2(200f, 76f),
                new Vector2(1f, 1f), new Vector2(-(Margin + 82f + 14f), -56f), "0", 38, OrangeText);

            // Each row says what the item DOES. The old shop showed a bare
            // label and a price with no explanation of the effect.
            (Button coinMagnetButton, Text coinMagnetLabel) = CreateShopRow(shopPanel.transform,
                "CoinMagnet", -178f, "코인 자석", "주변 코인을 끌어당깁니다");
            (Button redSkinButton, Text redSkinLabel) = CreateShopRow(shopPanel.transform,
                "RedSkin", -356f, "빨간 스킨", "도리의 색을 바꿉니다");

            BuildShopPreview(shopPanel.transform);

            CanvasGroup shopCanvasGroup = shopPanel.AddComponent<CanvasGroup>();
            shopCanvasGroup.alpha = 0f;
            shopPanel.AddComponent<PanelTransition>();
            shopPanel.SetActive(false);

            GameObject shopControllerGO = new GameObject("ShopController");
            ShopController shopController = shopControllerGO.AddComponent<ShopController>();
            shopController.SetReferences(shopPanel, currencyText, openButtons, coinMagnetButton,
                coinMagnetLabel, redSkinButton, redSkinLabel, closeButton);
        }

        /// <summary>
        /// One shop item: name, what it actually does, and the buy/equip
        /// button. The description line is the point - the old shop showed a
        /// bare label and a price, so nothing on screen said what buying it
        /// would change.
        /// </summary>
        private static (Button button, Text label) CreateShopRow(Transform parent, string name,
            float y, string title, string description)
        {
            GameObject row = CreatePanel(parent, name + "Row", UiSpriteGenerator.CreamPanelPath,
                new Vector2(640f, 160f), new Vector2(0.5f, 1f), new Vector2(0.5f, 1f), new Vector2(0f, y));

            CreateText(row.transform, "Title", title, 36, TextAnchor.LowerLeft,
                new Vector2(0f, 0.5f), new Vector2(0f, 0.5f), new Vector2(0f, 0f),
                new Vector2(340f, 46f), new Vector2(36f, 4f)).color = DarkText;

            CreateText(row.transform, "Description", description, 24, TextAnchor.UpperLeft,
                new Vector2(0f, 0.5f), new Vector2(0f, 0.5f), new Vector2(0f, 1f),
                new Vector2(340f, 40f), new Vector2(36f, -4f)).color = MutedText;

            (Button button, Text label) = CreateButton(row.transform, name + "Button", ButtonGreenPath,
                new Vector2(190f, 88f), new Vector2(1f, 0.5f), new Vector2(-30f, 0f), string.Empty, 32);
            AddOutline(label, 2f);

            return (button, label);
        }

        /// <summary>
        /// The character on a shelf, so a cosmetic can be judged before it is
        /// bought. Skipped entirely when no player sprite exists rather than
        /// leaving an empty frame on screen.
        /// </summary>
        private static void BuildShopPreview(Transform parent)
        {
            Sprite player = AssetDatabase.LoadAssetAtPath<Sprite>(PlayerSpritePath);
            if (player == null) return;

            GameObject preview = CreatePanel(parent, "PreviewCard", UiSpriteGenerator.CreamPanelPath,
                new Vector2(640f, 280f), new Vector2(0.5f, 0f), new Vector2(0.5f, 0f), new Vector2(0f, 52f));

            GameObject image = CreateRect(preview.transform, "Character",
                new Vector2(0f, 0.5f), new Vector2(0f, 0.5f), new Vector2(0f, 0.5f),
                new Vector2(150f, 212f), new Vector2(70f, 0f));
            Image characterImage = image.AddComponent<Image>();
            characterImage.sprite = player;
            characterImage.preserveAspect = true;

            CreateText(preview.transform, "Caption", "미리보기", 24, TextAnchor.LowerLeft,
                new Vector2(0.5f, 0.5f), new Vector2(0.5f, 0.5f), new Vector2(0f, 0f),
                new Vector2(260f, 40f), new Vector2(20f, 6f)).color = MutedText;

            CreateText(preview.transform, "Name", "기본 도리", 38, TextAnchor.UpperLeft,
                new Vector2(0.5f, 0.5f), new Vector2(0.5f, 0.5f), new Vector2(0f, 1f),
                new Vector2(260f, 56f), new Vector2(20f, -6f)).color = DarkText;
        }

        // ---- widget helpers --------------------------------------------------

        private static GameObject CreateRect(Transform parent, string name, Vector2 anchorMin, Vector2 anchorMax,
            Vector2 pivot, Vector2 sizeDelta, Vector2 anchoredPosition)
        {
            GameObject go = new GameObject(name, typeof(RectTransform));
            go.transform.SetParent(parent, false);

            RectTransform rect = go.GetComponent<RectTransform>();
            rect.anchorMin = anchorMin;
            rect.anchorMax = anchorMax;
            rect.pivot = pivot;
            rect.sizeDelta = sizeDelta;
            rect.anchoredPosition = anchoredPosition;
            return go;
        }

        private static GameObject CreateFullScreenPanel(Transform parent, string name, Color color)
        {
            GameObject panel = CreateRect(parent, name, Vector2.zero, Vector2.one, new Vector2(0.5f, 0.5f),
                Vector2.zero, Vector2.zero);
            Image image = panel.AddComponent<Image>();
            image.color = color;
            // Kept as a raycast target even when fully transparent: it is what
            // stops a tap on the title screen reaching the game behind it.
            image.raycastTarget = true;
            return panel;
        }

        /// <summary>A 9-sliced panel from one of the generated sprites, with the design's hard drop shadow.</summary>
        private static GameObject CreatePanel(Transform parent, string name, string spritePath,
            Vector2 size, Vector2 anchor, Vector2 pivot, Vector2 anchoredPosition)
        {
            GameObject go = CreateRect(parent, name, anchor, anchor, pivot, size, anchoredPosition);
            Image image = go.AddComponent<Image>();

            Sprite sprite = AssetDatabase.LoadAssetAtPath<Sprite>(spritePath);
            if (sprite != null)
            {
                image.sprite = sprite;
                image.type = Image.Type.Sliced;
                image.color = Color.white;
            }
            else
            {
                // Never invisible: a missing sprite falls back to the fill
                // colour rather than producing a panel nobody can see.
                image.color = new Color32(0xFF, 0xF6, 0xE4, 0xFF);
            }

            Shadow shadow = go.AddComponent<Shadow>();
            shadow.effectColor = new Color(Ink.r, Ink.g, Ink.b, 0.35f);
            shadow.effectDistance = new Vector2(0f, -9f);
            return go;
        }

        /// <summary>A rounded pill holding a coin icon and a number. Returns the number label.</summary>
        private static Text CreatePill(Transform parent, string name, Vector2 size,
            Vector2 anchor, Vector2 anchoredPosition, string value, int fontSize, Color valueColor)
        {
            GameObject pill = CreatePanel(parent, name, UiSpriteGenerator.CreamPanelPath,
                size, anchor, anchor, anchoredPosition);

            CreateSpriteImage(pill.transform, "CoinIcon", CoinSpritePath, new Vector2(36f, 36f),
                new Vector2(0f, 0.5f), new Vector2(30f, 0f));

            Text text = CreateText(pill.transform, "Value", value, fontSize, TextAnchor.MiddleRight,
                new Vector2(1f, 0.5f), new Vector2(1f, 0.5f), new Vector2(1f, 0.5f),
                new Vector2(size.x - 78f, size.y - 20f), new Vector2(-26f, 0f));
            text.color = valueColor;
            return text;
        }

        private static void CreateSpriteImage(Transform parent, string name, string spritePath,
            Vector2 size, Vector2 anchor, Vector2 anchoredPosition)
        {
            Sprite sprite = AssetDatabase.LoadAssetAtPath<Sprite>(spritePath);
            if (sprite == null) return;

            GameObject go = CreateRect(parent, name, anchor, anchor, anchor, size, anchoredPosition);
            Image image = go.AddComponent<Image>();
            image.sprite = sprite;
            image.preserveAspect = true;
        }

        private static (Button button, Text label) CreateButton(Transform parent, string name, string spritePath,
            Vector2 size, Vector2 anchor, Vector2 anchoredPosition, string label, int fontSize)
        {
            GameObject go = CreateRect(parent, name, anchor, anchor, anchor, size, anchoredPosition);
            Image image = go.AddComponent<Image>();
            StyleButton(image, spritePath);

            Button button = go.AddComponent<Button>();
            go.AddComponent<ButtonPunchFeedback>();

            Text text = CreateText(go.transform, "Label", label, fontSize, TextAnchor.MiddleCenter,
                Vector2.zero, Vector2.one, new Vector2(0.5f, 0.5f), Vector2.zero, Vector2.zero);

            return (button, text);
        }

        /// <summary>
        /// A small square button whose content is drawn from bars rather than
        /// typed as a character. The approved pack has no pause or close icon,
        /// and a glyph like "II" or a Unicode pause sign depends on whichever
        /// OS font KoreanFontApplier happens to find - two rectangles do not.
        /// </summary>
        private static Button CreateIconButton(Transform parent, string name, string spritePath,
            Vector2 size, Vector2 anchor, Vector2 anchoredPosition)
        {
            (Button button, Text label) = CreateButton(parent, name, spritePath, size, anchor,
                anchoredPosition, string.Empty, 1);
            // The empty label would still take a raycast and a draw call.
            Object.DestroyImmediate(label.gameObject);
            return button;
        }

        /// <summary>One bar of an icon: a plain coloured rect, optionally rotated.</summary>
        private static void CreateBar(Transform parent, string name, Vector2 size,
            Vector2 anchoredPosition, float rotationDegrees, Color color)
        {
            GameObject bar = CreateRect(parent, name, new Vector2(0.5f, 0.5f), new Vector2(0.5f, 0.5f),
                new Vector2(0.5f, 0.5f), size, anchoredPosition);
            bar.GetComponent<RectTransform>().localRotation = Quaternion.Euler(0f, 0f, rotationDegrees);
            Image image = bar.AddComponent<Image>();
            image.color = color;
            // The button under it already takes the tap; a child that also
            // does would break ButtonPunchFeedback's pointer handling.
            image.raycastTarget = false;
        }

        /// <summary>
        /// Applies a 9-sliced button sprite. Sliced so the 4px outline and the
        /// 8px bottom depth lip keep their thickness at any button size; the
        /// border itself is set by SharedArtImporter, measured from the file
        /// rather than guessed.
        ///
        /// Falls back to the blue button, and then to a flat colour, so
        /// generation never produces an invisible button on a machine where the
        /// colour variants have not been copied in yet.
        /// </summary>
        private static void StyleButton(Image image, string spritePath)
        {
            Sprite sprite = AssetDatabase.LoadAssetAtPath<Sprite>(spritePath)
                ?? AssetDatabase.LoadAssetAtPath<Sprite>(ButtonSpritePath);

            if (sprite == null)
            {
                image.color = new Color(0.2f, 0.6f, 0.9f);
                return;
            }

            image.sprite = sprite;
            image.type = Image.Type.Sliced;
            // Tinting multiplies, so a coloured fill would darken the art into
            // mud. White keeps the pack's own colour.
            image.color = Color.white;
        }

        /// <summary>
        /// The outlined-numeral look the whole casual genre runs on, using
        /// uGUI's own Outline effect - it draws the label four times at the
        /// given offset, which is why the distance stays small: a large one
        /// leaves visible gaps at the corners instead of a stroke.
        /// </summary>
        private static void AddOutline(Text text, float distance)
        {
            Outline outline = text.gameObject.AddComponent<Outline>();
            outline.effectColor = Ink;
            outline.effectDistance = new Vector2(distance, distance);
        }

        private static Text CreateText(Transform parent, string name, string content, int fontSize, TextAnchor alignment,
            Vector2 anchorMin, Vector2 anchorMax, Vector2 pivot, Vector2 sizeDelta, Vector2 anchoredPosition)
        {
            GameObject go = CreateRect(parent, name, anchorMin, anchorMax, pivot, sizeDelta, anchoredPosition);

            Text text = go.AddComponent<Text>();
            text.text = content;
            text.font = GetDefaultFont();
            text.fontSize = fontSize;
            text.alignment = alignment;
            text.color = Color.white;
            // Labels are Korean and the sizes here are tuned to the reference
            // resolution; letting a long string overflow its box reads better
            // than silently clipping a word in half.
            text.horizontalOverflow = HorizontalWrapMode.Overflow;
            text.verticalOverflow = VerticalWrapMode.Overflow;

            return text;
        }

        /// <summary>
        /// The Latin-only built-in font, replaced at runtime by
        /// KoreanFontApplier. It is still assigned here because a Text with no
        /// font at all renders nothing, and the applier needs something to
        /// replace if the OS offers no Korean face.
        /// </summary>
        private static Font GetDefaultFont()
        {
            if (cachedFont == null)
            {
                cachedFont = Resources.GetBuiltinResource<Font>("LegacyRuntime.ttf");
            }

            return cachedFont;
        }

        /// <summary>
        /// One persistent, manually-Emit()'d ParticleSystem for hit/collect
        /// bursts (see VfxManager). It is deliberately left looping and
        /// playing with rateOverTime at 0: that emits nothing by itself, but
        /// keeps the system simulating so a manual Emit() burst actually
        /// animates. A stopped system freezes the particles it is handed.
        ///
        /// MainModule.duration is intentionally not set - it is read-only in
        /// Unity's scripting API (assigning it is a compile error), and it is
        /// irrelevant to a looping system that never emits on its own clock.
        /// </summary>
        private static void CreateVfxManager()
        {
            GameObject vfxManagerGO = new GameObject("VfxManager");
            ParticleSystem particles = vfxManagerGO.AddComponent<ParticleSystem>();

            ParticleSystem.MainModule main = particles.main;
            main.playOnAwake = true;
            main.loop = true;
            main.startLifetime = 0.4f;
            main.startSpeed = 3f;
            main.startSize = 0.15f;
            main.simulationSpace = ParticleSystemSimulationSpace.World;

            ParticleSystem.EmissionModule emission = particles.emission;
            emission.rateOverTime = 0f;

            ParticleSystem.ShapeModule shape = particles.shape;
            shape.shapeType = ParticleSystemShapeType.Circle;
            shape.radius = 0.1f;

            // AddComponent'ing a ParticleSystem from script leaves its
            // renderer without a material, which renders nothing (or magenta)
            // - same class of bug as the Standard-vs-Unlit shader one, since
            // the generated scene has no Light either. Sprites/Default is
            // unlit and respects the per-particle startColor.
            // sharedMaterial, not material: assigning .material at edit time
            // makes Unity instantiate a copy and warn about leaking it into
            // the scene (same idiom as MainCharacterGenerator).
            ParticleSystemRenderer particleRenderer = vfxManagerGO.GetComponent<ParticleSystemRenderer>();
            Shader particleShader = Shader.Find("Sprites/Default");
            if (particleRenderer != null && particleShader != null)
            {
                particleRenderer.sharedMaterial = new Material(particleShader);
            }

            vfxManagerGO.AddComponent<VfxManager>();
        }

        /// <summary>
        /// Searches the generated scene's own hierarchy rather than calling
        /// Object.FindFirstObjectByType (obsolete in Unity 6.5, and it would
        /// also miss an EventSystem sitting inside the UI panels this
        /// generator deliberately saves disabled).
        /// </summary>
        private static void EnsureEventSystem(Scene scene)
        {
            foreach (GameObject root in scene.GetRootGameObjects())
            {
                if (root.GetComponentInChildren<EventSystem>(true) != null) return;
            }

            GameObject eventSystemGO = new GameObject("EventSystem");
            eventSystemGO.AddComponent<EventSystem>();
            eventSystemGO.AddComponent<StandaloneInputModule>();
        }

        private static void AddSceneToBuildSettings(string sceneAssetPath)
        {
            var scenes = new System.Collections.Generic.List<EditorBuildSettingsScene>(EditorBuildSettings.scenes);
            if (scenes.Exists(s => s.path == sceneAssetPath)) return;

            scenes.Add(new EditorBuildSettingsScene(sceneAssetPath, true));
            EditorBuildSettings.scenes = scenes.ToArray();
        }
    }
}
