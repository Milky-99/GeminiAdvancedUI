# -*- coding: utf-8 -*-
import os
import traceback
from io import BytesIO
from pathlib import Path
from typing import Optional, Tuple, Union

from PIL import Image, PngImagePlugin, ExifTags

from utils.constants import METADATA_KEY_UNRESOLVED_PROMPT, METADATA_KEY_RESOLVED_PROMPT
from utils.logger import log_error, log_info, log_warning, log_debug

# --- Pillow Metadata Handling ---
# Pillow writes EXIF slightly differently depending on version/OS sometimes.
# We will try to use standard tags where possible.
# For PNG, we use PngInfo chunks.

def _embed_metadata_png(image_path: Path, unresolved_prompt: str, resolved_prompt: str) -> bool:
    """Embeds prompts into PNG metadata using PngInfo."""
    try:
        img = Image.open(image_path)
        metadata = PngImagePlugin.PngInfo()
        metadata.add_text(METADATA_KEY_UNRESOLVED_PROMPT, unresolved_prompt)
        metadata.add_text(METADATA_KEY_RESOLVED_PROMPT, resolved_prompt)

        # Re-save the image with the new metadata
        img.save(image_path, "PNG", pnginfo=metadata)
        img.close()
        log_info(f"Prompts embedded successfully in PNG: {image_path.name}")
        return True
    except FileNotFoundError:
        log_error(f"File not found for embedding PNG metadata: {image_path}")
        return False
    except Exception as e:
        log_error(f"Error embedding prompts in PNG {image_path.name}: {e}", exc_info=True)
        return False

def _embed_metadata_jpeg(image_path: Path, unresolved_prompt: str, resolved_prompt: str) -> bool:
    """Embeds prompts into JPEG metadata using EXIF."""
    try:
        img = Image.open(image_path)
        exif_dict = {}
        if "exif" in img.info:
            try:
                 exif_dict = Image.Exif.loads(img.info["exif"])
            except Exception as exif_load_err:
                 log_warning(f"Could not load existing EXIF data from {image_path.name}: {exif_load_err}. Creating new EXIF.")

        # Find the integer keys for standard tags if possible, otherwise use fallback keys
        # This part is complex due to inconsistent tag mapping.
        # For simplicity, we'll use common integer tags directly.
        # UserComment (0x9286), XPComment (0x9c9c) are common places
        # We use the string names defined in constants.py which Pillow might map correctly or store directly.
        # Note: Pillow < 9 might handle this differently.
        user_comment_tag = None
        xp_comment_tag = None

        # Find the integer tag codes for common comment fields
        for tag, name in ExifTags.TAGS.items():
             if name == METADATA_KEY_UNRESOLVED_PROMPT: # Usually 'UserComment'
                  user_comment_tag = tag
             elif name == METADATA_KEY_RESOLVED_PROMPT: # Usually 'XPComment'
                  xp_comment_tag = tag

        if user_comment_tag is None:
            user_comment_tag = 0x9286 # Fallback to common UserComment tag ID
            log_debug(f"Using fallback EXIF tag ID {hex(user_comment_tag)} for unresolved prompt.")
        if xp_comment_tag is None:
            xp_comment_tag = 0x9c9c # Fallback to common XPComment tag ID
            log_debug(f"Using fallback EXIF tag ID {hex(xp_comment_tag)} for resolved prompt.")

        # EXIF strings often need specific encoding (e.g., UTF-16LE for XPComment)
        # Pillow >= 9 might handle utf-8 better. Let's try utf-8 first.
        try:
             exif_dict[user_comment_tag] = unresolved_prompt.encode('utf-8')
             exif_dict[xp_comment_tag] = resolved_prompt.encode('utf-8')
        except UnicodeEncodeError:
             log_warning("UTF-8 encoding failed for EXIF, trying UTF-16LE (might be needed for XPComment).")
             try:
                  # XPComment often expects UCS2/UTF-16LE, prefixed with encoding identifier
                  # Try encoding identifier 'ASCII\x00\x00\x00' + utf-8 first (sometimes works)
                  # exif_dict[user_comment_tag] = b'ASCII\x00\x00\x00' + unresolved_prompt.encode('utf-8')
                  # exif_dict[xp_comment_tag] = b'ASCII\x00\x00\x00' + resolved_prompt.encode('utf-8')
                  # More reliably, use UTF-16LE for XPComment
                  exif_dict[user_comment_tag] = unresolved_prompt.encode('utf-8') # UserComment often okay with UTF-8
                  xp_comment_bytes = b'UNICODE\x00' + resolved_prompt.encode('utf-16le')
                  exif_dict[xp_comment_tag] = xp_comment_bytes

             except Exception as enc_err:
                  log_error(f"Failed to encode prompts for EXIF: {enc_err}")
                  img.close()
                  return False


        try:
            exif_bytes = Image.Exif.dump(exif_dict)
        except Exception as dump_err:
            log_error(f"Failed to dump EXIF dictionary: {dump_err}")
            img.close()
            return False

        # Re-save the image with the new EXIF data
        img.save(image_path, "JPEG", exif=exif_bytes, quality=95, optimize=True) # Adjust quality as needed
        img.close()
        log_info(f"Prompts embedded successfully in JPEG: {image_path.name}")
        return True
    except FileNotFoundError:
        log_error(f"File not found for embedding JPEG metadata: {image_path}")
        return False
    except Exception as e:
        log_error(f"Error embedding prompts in JPEG {image_path.name}: {e}", exc_info=True)
        return False


def _extract_metadata_png(image_path: Path) -> Tuple[Optional[str], Optional[str]]:
    """Extracts prompts from PNG metadata."""
    unresolved = None
    resolved = None
    try:
        img = Image.open(image_path)
        if img.format == "PNG" and img.info:
            unresolved = img.info.get(METADATA_KEY_UNRESOLVED_PROMPT)
            resolved = img.info.get(METADATA_KEY_RESOLVED_PROMPT)
            log_debug(f"Extracted PNG metadata from {image_path.name}: Unresolved='{unresolved is not None}', Resolved='{resolved is not None}'")
        img.close()
    except FileNotFoundError:
         log_error(f"File not found for extracting PNG metadata: {image_path}")
    except Exception as e:
        log_error(f"Error extracting PNG metadata from {image_path.name}: {e}")
    return unresolved, resolved

def _extract_metadata_jpeg(image_path: Path) -> Tuple[Optional[str], Optional[str]]:
    """Extracts prompts from JPEG EXIF metadata."""
    unresolved = None
    resolved = None
    try:
        img = Image.open(image_path)
        exif_data = img.getexif() # Use getexif() for easier access

        if exif_data:
             user_comment_tag = None
             xp_comment_tag = None
             for tag, name in ExifTags.TAGS.items():
                 if name == METADATA_KEY_UNRESOLVED_PROMPT: user_comment_tag = tag
                 elif name == METADATA_KEY_RESOLVED_PROMPT: xp_comment_tag = tag
             if user_comment_tag is None: user_comment_tag = 0x9286
             if xp_comment_tag is None: xp_comment_tag = 0x9c9c

             user_comment_bytes = exif_data.get(user_comment_tag)
             xp_comment_bytes = exif_data.get(xp_comment_tag)

             # Try decoding UserComment (often utf-8 or ascii)
             if user_comment_bytes:
                 try: unresolved = user_comment_bytes.decode('utf-8').strip('\x00')
                 except UnicodeDecodeError:
                     try: unresolved = user_comment_bytes.decode('latin-1').strip('\x00') # Common fallback
                     except Exception: log_warning(f"Could not decode UserComment bytes in {image_path.name}")

             # Try decoding XPComment (often prefixed UCS2/UTF-16LE)
             if xp_comment_bytes:
                 try:
                     # Check for standard prefixes
                     if xp_comment_bytes.startswith(b'UNICODE\x00'):
                          resolved = xp_comment_bytes[8:].decode('utf-16le').strip('\x00')
                     elif xp_comment_bytes.startswith(b'ASCII\x00\x00\x00'):
                          resolved = xp_comment_bytes[8:].decode('ascii').strip('\x00') # Or maybe utf-8? try utf-8
                          try:
                               resolved = xp_comment_bytes[8:].decode('utf-8').strip('\x00')
                          except:
                               resolved = xp_comment_bytes[8:].decode('ascii').strip('\x00') # Fallback to ascii
                     else:
                          # Assume UTF-8 or Latin-1 if no prefix
                          try: resolved = xp_comment_bytes.decode('utf-8').strip('\x00')
                          except UnicodeDecodeError: resolved = xp_comment_bytes.decode('latin-1').strip('\x00')
                 except Exception as dec_err:
                      log_warning(f"Could not decode XPComment bytes in {image_path.name}: {dec_err}")

             log_debug(f"Extracted JPEG metadata from {image_path.name}: Unresolved='{unresolved is not None}', Resolved='{resolved is not None}'")

        img.close()
    except FileNotFoundError:
        log_error(f"File not found for extracting JPEG metadata: {image_path}")
    except Exception as e:
        log_error(f"Error extracting JPEG metadata from {image_path.name}: {e}")
    return unresolved, resolved


class ImageProcessor:
    """Handles image loading, saving, and metadata operations."""

    @staticmethod
    def save_image(image_bytes: bytes, filename: Path) -> bool:
        """Saves image bytes to a file using Pillow."""
        try:
            log_debug(f"Attempting to open image data with Pillow for saving to {filename}...")
            image = Image.open(BytesIO(image_bytes))
            image.load() # Verify image data is readable
            log_debug(f"Image loaded (Format: {image.format}, Size: {image.size}, Mode: {image.mode})")

            # Ensure parent directory exists
            filename.parent.mkdir(parents=True, exist_ok=True)

            log_info(f"Saving image as '{filename}'...")
            # Preserve format if possible, default to PNG otherwise
            save_format = image.format if image.format else "PNG"
            image.save(filename, format=save_format)
            log_info(f"Image successfully saved.")
            image.close()
            return True
        except Exception as img_err:
            log_error(f"Error processing/saving image with Pillow: {img_err}", exc_info=True)
            return False

    @staticmethod
    def embed_prompts_in_image(image_path: Path, unresolved_prompt: str, resolved_prompt: str) -> bool:
        """Embeds prompts into image metadata based on file type."""
        if not image_path.exists():
            log_error(f"Cannot embed metadata, file does not exist: {image_path}")
            return False

        ext = image_path.suffix.lower()
        if ext == ".png":
            return _embed_metadata_png(image_path, unresolved_prompt, resolved_prompt)
        elif ext in [".jpg", ".jpeg"]:
            return _embed_metadata_jpeg(image_path, unresolved_prompt, resolved_prompt)
        else:
            log_warning(f"Metadata embedding not supported for file type: {ext}")
            return False

    @staticmethod
    def extract_prompts_from_image(image_path: Path) -> Tuple[Optional[str], Optional[str]]:
        """Extracts prompts from image metadata based on file type."""
        if not image_path.exists():
            log_error(f"Cannot extract metadata, file does not exist: {image_path}")
            return None, None

        ext = image_path.suffix.lower()
        log_debug(f"Attempting metadata extraction for {image_path.name} (type: {ext})")
        if ext == ".png":
            return _extract_metadata_png(image_path)
        elif ext in [".jpg", ".jpeg"]:
            return _extract_metadata_jpeg(image_path)
        else:
            log_warning(f"Metadata extraction not supported for file type: {ext}")
            return None, None

    @staticmethod
    def load_image_for_api(image_path: Path) -> Optional[Image.Image]:
        """Loads an image file into a Pillow Image object for API use."""
        if not image_path.exists() or not image_path.is_file():
             log_error(f"Image file not found or is not a file: {image_path}")
             return None
        try:
            img = Image.open(image_path)
            img.load() # Ensure image data is loaded
            log_info(f"Image loaded for API: {image_path.name} (Format: {img.format}, Size: {img.size}, Mode: {img.mode})")
            return img
        except Exception as e:
             log_error(f"Failed to load image {image_path} with Pillow: {e}", exc_info=True)
             return None

    @staticmethod
    def create_thumbnail_bytes(image_source: Union[Path, bytes], size: Tuple[int, int] = (256, 256)) -> Optional[bytes]:
         """
         Creates a thumbnail from an image source (file path or bytes)
         and returns its bytes (PNG format).

         Args:
             image_source: A pathlib.Path to the image file or raw bytes of the image.
             size: The desired size of the thumbnail (width, height).

         Returns:
             The bytes of the thumbnail image in PNG format, or None on failure.
         """
         try:
              if isinstance(image_source, Path):
                   if not image_source.exists() or not image_source.is_file():
                        log_error(f"Cannot create thumbnail, file not found: {image_source}")
                        return None
                   img = Image.open(image_source)
                   log_debug(f"Opened image from path for thumbnail: {image_source.name}")
              elif isinstance(image_source, bytes):
                   img = Image.open(BytesIO(image_source))
                   log_debug("Opened image from bytes for thumbnail.")
              else:
                   log_error(f"Invalid image_source type for thumbnail creation: {type(image_source)}")
                   return None

              img.thumbnail(size)
              byte_io = BytesIO()
              img.save(byte_io, "PNG") # Save thumbnail as PNG
              img.close()
              byte_io.seek(0)
              log_debug("Thumbnail bytes created successfully.")
              return byte_io.getvalue()
         except Exception as e:
              log_error(f"Failed to create thumbnail from source {image_source}: {e}", exc_info=True)
              return None