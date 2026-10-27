# Edge Device Test Suite

## Dependencies
Before building and running the tests on a brand-new Ubuntu VM, ensure you have the required build tools and cryptography libraries installed:
```bash
sudo apt update
sudo apt install -y build-essential cmake libssl-dev
```
## How to Build and Run Tests
1. Navigate to the edge-device/ directory.
2. Create a build directory and compile using CMake:
```bash 
mkdir build && cd build
cmake ..
make
```
3. Execute the test runner binary:
```bash
./test_monitoring
```
## Test Coverage
The test executable (test_monitoring) validates:
- Nominal Memory Usage: Asserts memory stays under the 2 GB limit under normal load.
- Negative Constraint Checks: Validates that artificial memory breaches are successfully flagged.
- FSM Timing Latency: Asserts state transition evaluation executes within $\le 50$ ms.
- Audit Chain Integrity & Tamper Detection: Verifies that SHA-256 hash chains instantly catch external line modifications or file corruption/truncation.
