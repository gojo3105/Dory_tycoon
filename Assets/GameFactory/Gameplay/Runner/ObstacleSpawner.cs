using System;
using GameFactory.Core;
using UnityEngine;

namespace GameFactory.Gameplay.Runner
{
    /// <summary>
    /// Spawns pooled obstacles ahead of the player at a difficulty-scaled
    /// random gap, in two kinds: something on the ground to jump over, and
    /// something hanging overhead to slide under.
    ///
    /// The second kind is what makes the level worth reading. One kind of
    /// obstacle means the player only ever judges WHEN; two mean they have to
    /// judge WHICH, and that is the difference between a timing test and a
    /// game. Overhead bars are only spawned when the GameSpec turns the slide
    /// on - an obstacle the player has no verb for is not difficulty, it is a
    /// wall.
    /// </summary>
    public class ObstacleSpawner : MonoBehaviour
    {
        [SerializeField] private GameObject obstaclePrefab;
        [SerializeField] private GameObject overheadPrefab;
        [SerializeField] private Transform player;
        // Deliberately further ahead than the coin spawner's 12. Obstacles
        // have to exist before coins are placed, or a coin gets dropped inside
        // a hanging bar - drawn as a reward, sitting in something that kills.
        [SerializeField] private float spawnAheadDistance = 16f;
        [SerializeField] private float groundY;
        [SerializeField] private float overheadY = 1.5f;
        [SerializeField] private float minGap = 4f;
        [SerializeField] private float maxGap = 8f;

        [Tooltip("Share of obstacles that hang overhead, once the slide is available.")]
        [Range(0f, 1f)]
        [SerializeField] private float overheadChance = 0.4f;

        /// <summary>Obstacles at the start of a run that are always jumpable, so the first thing a new player meets is the verb they already know.</summary>
        private const int WarmupSpawns = 2;

        private GameObjectPool groundPool;
        private GameObjectPool overheadPool;
        private float nextSpawnX;
        private bool overheadEnabled;
        private bool lastWasOverhead;
        private int spawnCount;

        /// <summary>Wires structural references. Called at edit time by SceneGenerator.</summary>
        public void SetReferences(GameObject prefab, Transform playerTransform, float ground)
        {
            obstaclePrefab = prefab;
            player = playerTransform;
            groundY = ground;
        }

        /// <summary>
        /// Wires the overhead variant. Separate from SetReferences so a scene
        /// generated before overhead obstacles existed still wires up, and
        /// simply never spawns them.
        /// </summary>
        public void SetOverheadReferences(GameObject prefab, float y)
        {
            overheadPrefab = prefab;
            overheadY = y;
        }

        // A gap must be longer than the jump that has to clear it, or the
        // player lands on the next obstacle no matter how well they time it.
        // 1.6x leaves room to land, recover and take off again; 3.0x is the
        // longest breather before the level reads as empty.
        private const float MinGapPerJump = 1.6f;
        private const float MaxGapPerJump = 3.0f;

        /// <summary>Applies GameSpec-driven tuning. Called at runtime by RunnerGameInitializer.
        ///
        /// jumpDistance is how far one jump actually carries the player, from
        /// RunnerPlayerController.JumpDistance. Spacing was a pair of fixed
        /// numbers before, so with the real arc of 12.2 units against a 4-8
        /// gap a single jump sailed over two or three obstacles.
        /// </summary>
        public void Configure(float levelLength, string difficulty, float jumpDistance = 0f,
                             bool slideEnabled = false)
        {
            overheadEnabled = slideEnabled && overheadPrefab != null;

            float scale = string.Equals(difficulty, "Hard", StringComparison.OrdinalIgnoreCase) ? 0.8f
                : string.Equals(difficulty, "Easy", StringComparison.OrdinalIgnoreCase) ? 1.25f
                : 1f;

            // Falling back to the old constants when the arc is unknown keeps
            // an older scene working rather than spacing everything at zero.
            float reach = jumpDistance > 0.1f ? jumpDistance : 2.5f;

            minGap = Mathf.Max(2f, reach * MinGapPerJump * scale);
            maxGap = Mathf.Max(minGap + reach * 0.5f, reach * MaxGapPerJump * scale);
        }

        private void Awake()
        {
            groundPool = gameObject.AddComponent<GameObjectPool>();
            groundPool.Initialize(obstaclePrefab, 6);

            if (overheadPrefab != null)
            {
                // A pool of its own: the two prefabs are different objects, and
                // one queue handing back the wrong one would put a hanging bar
                // on the ground.
                overheadPool = gameObject.AddComponent<GameObjectPool>();
                overheadPool.Initialize(overheadPrefab, 4);
            }

            nextSpawnX = (player != null ? player.position.x : 0f) + spawnAheadDistance;
        }

        private void Update()
        {
            if (player == null) return;

            while (nextSpawnX < player.position.x + spawnAheadDistance)
            {
                SpawnAt(nextSpawnX);
                nextSpawnX += UnityEngine.Random.Range(minGap, maxGap);
            }
        }

        private void SpawnAt(float x)
        {
            // Never two overhead bars in a row: standing up out of one slide
            // only to duck again is a mash, not a decision.
            bool overhead = overheadEnabled
                            && overheadPool != null
                            && spawnCount >= WarmupSpawns
                            && !lastWasOverhead
                            && UnityEngine.Random.value < overheadChance;

            GameObjectPool source = overhead ? overheadPool : groundPool;
            float y = overhead ? overheadY : groundY;

            GameObject instance = source.Get(new Vector3(x, y, 0f), Quaternion.identity);
            RecycleWhenPassed recycle = instance.GetComponent<RecycleWhenPassed>();
            if (recycle == null) recycle = instance.AddComponent<RecycleWhenPassed>();
            // The pool it came from, not the default one - returning a bar to
            // the ground pool would hand it out later as a ground obstacle.
            recycle.Initialize(source, player);

            lastWasOverhead = overhead;
            spawnCount++;
        }
    }
}
