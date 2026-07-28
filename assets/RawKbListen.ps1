param([int]$Seconds = 40)

Add-Type -ReferencedAssemblies System.Windows.Forms, System.Drawing -TypeDefinition @"
using System;
using System.Runtime.InteropServices;
using System.Windows.Forms;
using System.Text;

public class RawKbListener : Form {
    const int WM_INPUT = 0x00FF;
    const uint RID_INPUT = 0x10000003;
    const uint RIDI_DEVICENAME = 0x20000007;

    [StructLayout(LayoutKind.Sequential)]
    public struct RAWINPUTDEVICE { public ushort UsagePage; public ushort Usage; public uint Flags; public IntPtr Target; }
    [StructLayout(LayoutKind.Sequential)]
    public struct RAWINPUTHEADER { public uint Type; public uint Size; public IntPtr Device; public IntPtr wParam; }
    [StructLayout(LayoutKind.Sequential)]
    public struct RAWKEYBOARD { public ushort MakeCode; public ushort Flags; public ushort Reserved; public ushort VKey; public uint Message; public uint ExtraInformation; }
    [StructLayout(LayoutKind.Sequential)]
    public struct RAWINPUT { public RAWINPUTHEADER Header; public RAWKEYBOARD Keyboard; }

    [DllImport("user32.dll", SetLastError=true)]
    static extern bool RegisterRawInputDevices(RAWINPUTDEVICE[] pRawInputDevices, uint uiNumDevices, uint cbSize);
    [DllImport("user32.dll")]
    static extern uint GetRawInputData(IntPtr hRawInput, uint uiCommand, IntPtr pData, ref uint pcbSize, uint cbSizeHeader);
    [DllImport("user32.dll", CharSet=CharSet.Ansi)]
    static extern uint GetRawInputDeviceInfoA(IntPtr hDevice, uint uiCommand, IntPtr pData, ref uint pcbSize);

    public RawKbListener() {
        this.ShowInTaskbar = false;
        this.Opacity = 0;
        this.FormBorderStyle = FormBorderStyle.None;
        this.Size = new System.Drawing.Size(1, 1);
        RAWINPUTDEVICE[] rid = new RAWINPUTDEVICE[3];
        rid[0].UsagePage = 0x01;   // Generic Desktop
        rid[0].Usage = 0x06;       // Keyboard
        rid[0].Flags = 0x00000100; // RIDEV_INPUTSINK: receive even without focus
        rid[0].Target = this.Handle;
        rid[1].UsagePage = 0x0C;   // Consumer page (media/special keys)
        rid[1].Usage = 0x01;       // Consumer Control
        rid[1].Flags = 0x00000100;
        rid[1].Target = this.Handle;
        rid[2].UsagePage = 0x01;
        rid[2].Usage = 0x80;       // System Control (power/sleep keys)
        rid[2].Flags = 0x00000100;
        rid[2].Target = this.Handle;
        if (!RegisterRawInputDevices(rid, 3, (uint)Marshal.SizeOf(typeof(RAWINPUTDEVICE))))
            Console.WriteLine("ERROR: RegisterRawInputDevices failed");
        else
            Console.WriteLine("LISTENING: press keys now (Home on each keyboard)...");
    }

    string DeviceName(IntPtr h) {
        if (h == IntPtr.Zero) return "(no-handle:injected)";
        uint size = 0;
        GetRawInputDeviceInfoA(h, RIDI_DEVICENAME, IntPtr.Zero, ref size);
        if (size == 0) return "(handle=0x" + h.ToString("X") + " name-query-failed)";
        IntPtr p = Marshal.AllocHGlobal((int)size + 1);
        GetRawInputDeviceInfoA(h, RIDI_DEVICENAME, p, ref size);
        string s = Marshal.PtrToStringAnsi(p);
        Marshal.FreeHGlobal(p);
        if (s == null || s.Length == 0)
            return "(handle=0x" + h.ToString("X") + " empty-name)";
        return s;
    }

    protected override void WndProc(ref Message m) {
        if (m.Msg == WM_INPUT) {
            uint size = 0;
            uint hdr = (uint)Marshal.SizeOf(typeof(RAWINPUTHEADER));
            GetRawInputData(m.LParam, RID_INPUT, IntPtr.Zero, ref size, hdr);
            IntPtr buf = Marshal.AllocHGlobal((int)size);
            try {
                if (GetRawInputData(m.LParam, RID_INPUT, buf, ref size, hdr) == size) {
                    RAWINPUT ri = (RAWINPUT)Marshal.PtrToStructure(buf, typeof(RAWINPUT));
                    if (ri.Header.Type == 1 && (ri.Keyboard.Flags & 1) == 0) { // keyboard, key-down only
                        Console.WriteLine(String.Format(
                            "KEYDOWN vk=0x{0:X2} key=[{1}] sc=0x{2:X2} flags=0x{3:X} | device={4}",
                            ri.Keyboard.VKey, (Keys)ri.Keyboard.VKey, ri.Keyboard.MakeCode,
                            ri.Keyboard.Flags, DeviceName(ri.Header.Device)));
                    } else if (ri.Header.Type == 2) { // RIM_TYPEHID: consumer/system reports
                        int hdrSize = Marshal.SizeOf(typeof(RAWINPUTHEADER));
                        uint sizeHid = (uint)Marshal.ReadInt32(buf, hdrSize);
                        uint count = (uint)Marshal.ReadInt32(buf, hdrSize + 4);
                        var sb = new StringBuilder();
                        int dataOff = hdrSize + 8;
                        for (int i = 0; i < Math.Min(sizeHid * count, 16); i++)
                            sb.AppendFormat("{0:X2} ", Marshal.ReadByte(buf, dataOff + i));
                        Console.WriteLine("HIDRAW bytes=[" + sb.ToString().Trim() +
                                          "] | device=" + DeviceName(ri.Header.Device));
                    }
                }
            } finally { Marshal.FreeHGlobal(buf); }
        }
        base.WndProc(ref m);
    }
}
"@

$form = New-Object RawKbListener
$timer = New-Object System.Windows.Forms.Timer
$timer.Interval = $Seconds * 1000
$timer.Add_Tick({ [System.Windows.Forms.Application]::Exit() })
$timer.Start()
[System.Windows.Forms.Application]::Run($form)
Write-Host "DONE listening."
