using GameFactory.Gameplay.Runner;
using UnityEngine;
using UnityEngine.EventSystems;

namespace GameFactory.UI
{
    /// <summary>Large, hold-aware mobile action button for the runner HUD.</summary>
    public class RunnerActionButton : MonoBehaviour, IPointerDownHandler, IPointerUpHandler, IPointerExitHandler
    {
        public enum ActionKind { Jump, Slide }

        [SerializeField] private RunnerPlayerController player;
        [SerializeField] private ActionKind action;

        public void SetReferences(RunnerPlayerController controller, ActionKind kind)
        {
            player = controller;
            action = kind;
        }

        public void OnPointerDown(PointerEventData eventData)
        {
            if (action == ActionKind.Jump) player?.RequestJump();
            else player?.RequestSlide(true);
        }

        public void OnPointerUp(PointerEventData eventData)
        {
            if (action == ActionKind.Slide) player?.RequestSlide(false);
        }

        public void OnPointerExit(PointerEventData eventData)
        {
            if (action == ActionKind.Slide) player?.RequestSlide(false);
        }
    }
}
