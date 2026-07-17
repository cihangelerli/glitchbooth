import time
from escpos.printer import File

def test_printer():
    print("Connecting to Xprinter on /dev/usb/lp0...")
    # Using the File device connection type for raw Linux character devices
    printer = File("/dev/usb/lp0")

    print("Sending text data...")
    # Initialize printer to clear buffer
    printer.hw("INIT")
    
    # Print custom text
    printer.text("================================\n")
    printer.text("      GLITCH BOOTH TEST         \n")
    printer.text("================================\n")
    printer.text("If you see this, Python printing\n")
    printer.text("is working perfectly!\n\n")
    
    # Feed 3 lines so the cut happens past the text
    printer.ln(3)
    
    print("Sending cut command...")
    # Cut paper automatically
    printer.cut()
    
    print("Done! Check your printer.")

if __name__ == "__main__":
    test_printer()

