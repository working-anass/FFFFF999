import torch
from diffusers import DiffusionPipeline
import os

def generate_images(prompt, guidance_scale, num_inference_steps, height, width, output_dir, num_images, pipe):
    # Create output directory if it doesn't exist
    os.makedirs(output_dir, exist_ok=True)

    for i in range(1, num_images + 1):
        print(f"Generating image {i} for prompt: {prompt}")
        image = pipe(
            prompt=prompt,
            guidance_scale=guidance_scale,
            num_inference_steps=num_inference_steps,
            height=height,
            width=width
        ).images[0]

        # Clean filename
        safe_prompt = "_".join(prompt.strip().split())
        output_path = os.path.join(output_dir, f"{safe_prompt}_{i}.png")
        image.save(output_path)
        print(f"Image saved to {output_path}")

def main():
    # Settings
    guidance_scale = 7.5
    num_inference_steps = 25
    height = 768
    width = 768
    output_dir = "outputs"
    num_images = 15

    # Determine device and dtype
    device = "cuda" if torch.cuda.is_available() else "cpu"
    dtype = torch.float16 if device == "cuda" else torch.float32

    # Load the pipeline
    print("Loading model...")
    pipe = DiffusionPipeline.from_pretrained(
        "black-forest-labs/FLUX.1-schnell",
        torch_dtype=dtype
    )
    pipe.to(device)

    # Read prompts from file
    with open("prompts.txt", "r", encoding="utf-8") as f:
        prompts = [line.strip() for line in f if line.strip()]

    for prompt in prompts:
        generate_images(
            prompt=prompt,
            guidance_scale=guidance_scale,
            num_inference_steps=num_inference_steps,
            height=height,
            width=width,
            output_dir=output_dir,
            num_images=num_images,
            pipe=pipe
        )

if __name__ == "__main__":
    main()
