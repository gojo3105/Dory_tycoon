using GameFactory.Core;
using UnityEngine;

namespace GameFactory.Gameplay.Runner
{
    /// <summary>Spawns pooled, collectible coins ahead of the player at a fixed spacing.</summary>
    public class CoinSpawner : MonoBehaviour
    {
        [SerializeField] private GameObject coinPrefab;
        [SerializeField] private Transform player;
        [SerializeField] private float spawnAheadDistance = 12f;
        [SerializeField] private float coinY = 1f;
        [SerializeField] private float spacing = 3f;

        private GameObjectPool pool;
        private float nextSpawnX;
        private int spawnIndex;

        /// <summary>Wires structural references. Called at edit time by SceneGenerator.</summary>
        public void SetReferences(GameObject prefab, Transform playerTransform, float y)
        {
            coinPrefab = prefab;
            player = playerTransform;
            coinY = y;
        }

        /// <summary>Applies GameSpec-driven tuning. Called at runtime by RunnerGameInitializer.</summary>
        public void Configure(float levelLength)
        {
            spacing = Mathf.Clamp(levelLength / 100f, 1.1f, 1.8f);
        }

        private void Awake()
        {
            pool = gameObject.AddComponent<GameObjectPool>();
            pool.Initialize(coinPrefab, 10);
            nextSpawnX = (player != null ? player.position.x : 0f) + spawnAheadDistance;
        }

        private void Update()
        {
            if (player == null) return;

            while (nextSpawnX < player.position.x + spawnAheadDistance)
            {
                SpawnAt(nextSpawnX);
                nextSpawnX += spacing;
            }
        }

        /// <summary>
        /// How much clear space a coin needs around it. Wide, so one never
        /// sits flush against the edge of a hanging bar - close enough to
        /// touch and impossible to take. Deliberately SHORT: a coin floating
        /// just above a ground obstacle is good design, the reward for the
        /// jump you already had to make, and a taller probe would delete it.
        /// </summary>
        private static readonly Vector2 ClearanceProbe = new Vector2(1.4f, 0.7f);

        private void SpawnAt(float x)
        {
            int phase = (spawnIndex / 7) % 3;
            float t = (spawnIndex % 7) / 6f;
            float y = coinY;
            if (phase == 1) y += Mathf.Sin(t * Mathf.PI) * 1.45f;
            else if (phase == 2) y += (0.5f + 0.5f * Mathf.Sin(t * Mathf.PI * 2f)) * 0.8f;
            spawnIndex++;

            // Overhead bars are tall, and the coin line runs straight through
            // where they hang. A coin drawn inside one reads as a reward and
            // is really a kill box: the player jumps for it and dies. The
            // obstacle spawner deliberately runs further ahead than this one,
            // so by the time a coin is placed the bar at that x already exists
            // and this query can see it.
            Collider2D blocking = Physics2D.OverlapBox(new Vector2(x, y), ClearanceProbe, 0f);
            if (blocking != null && blocking.CompareTag("Obstacle")) return;

            GameObject instance = pool.Get(new Vector3(x, y, 0f), Quaternion.identity);

            RecycleWhenPassed recycle = instance.GetComponent<RecycleWhenPassed>();
            if (recycle == null) recycle = instance.AddComponent<RecycleWhenPassed>();
            recycle.Initialize(pool, player);

            Coin coin = instance.GetComponent<Coin>();
            if (coin != null) coin.ResetState();
        }
    }
}
