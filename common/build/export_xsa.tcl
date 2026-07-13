if {$argc != 4} {
    puts stderr "Usage: export_xsa.tcl XPR XSA IMPL_RUN EXPECTED_BOARD_PART"
    exit 2
}

set xpr_path [file normalize [lindex $argv 0]]
set xsa_path [file normalize [lindex $argv 1]]
set impl_run [lindex $argv 2]
set expected_board_part [lindex $argv 3]

open_project $xpr_path

if {$expected_board_part ne ""} {
    set configured_board_part [get_property BOARD_PART [current_project]]
    if {$configured_board_part ne $expected_board_part} {
        error "Project board part '$configured_board_part' does not match expected '$expected_board_part'"
    }
    if {[llength [get_board_parts -quiet $expected_board_part]] == 0} {
        error "Required Vivado board definition is not installed: $expected_board_part"
    }
}

set implementation_runs [get_runs -quiet $impl_run]
if {[llength $implementation_runs] != 1} {
    error "Vivado implementation run '$impl_run' does not exist. Compile the PL project before running make_PL.sh."
}

set run_status [get_property STATUS $implementation_runs]
if {![string match "*write_bitstream Complete*" $run_status]} {
    error "Vivado implementation run '$impl_run' has no completed bitstream (status: $run_status). Compile the PL project before running make_PL.sh."
}

file mkdir [file dirname $xsa_path]
write_hw_platform -fixed -include_bit -force -file $xsa_path

if {![file exists $xsa_path]} {
    error "Vivado did not create the requested XSA: $xsa_path"
}

close_project
puts "Created XSA: $xsa_path"

