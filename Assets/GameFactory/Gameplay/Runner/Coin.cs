using GameFactory.Core;
using UnityEngine;

namespace GameFactory.Gameplay.Runner
{
    /// <summary>Adds currency and returns itself to its pool when the player touches it.</summary>
    [RequireComponent(typeof(Collider2D))]
    public class Coin : MonoBehaviour
    {
        [SerializeField] private int coinValue = 1;
        [SerializeField] private string playerTag = "Player";

        private bool collected;

        private static readonly Color CollectVfxColor = new Color(1f, 0.85f, 0.2f);
        private static AudioClip collectClip;

        private void Reset()
        {
            Collider2D col = GetComponent<Collider2D>();
            if (col != null) col.isTrigger = true;
        }

        /// <summary>Called by CoinSpawner when this pooled instance is reused.</summary>
        public void ResetState() => collected = false;

        /// <summary>Collects this coin. Called directly by CoinMagnet, or via the trigger below on direct player contact.</summary>
        public void Collect()
        {
            if (collected) return;

            collected = true;
            GameManager.Instance.AddCoins(coinValue);
            RunnerEnergy.Instance?.RefillFromPickup();

            if (collectClip == null) collectClip = ProceduralTone.Sine("SFX_Coin", 1200f, 0.1f);
            AudioManager.Instance?.PlaySfx(collectClip);
            VfxManager.Instance?.PlayBurst(transform.position, CollectVfxColor, 0.12f, 10);

            GetComponent<RecycleWhenPassed>()?.ReleaseNow();
        }

        private void OnTriggerEnter2D(Collider2D other)
        {
            if (!other.CompareTag(playerTag)) return;
            Collect();
        }
    }
}
