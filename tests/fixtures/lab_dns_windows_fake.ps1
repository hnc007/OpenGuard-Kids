# Fake Windows boundary. Installed only into a temporary copy of the lab script by pytest.
function Get-LabAdapter([int]$Index) {
    if ($Index -ne $global:LabAdapterFixture.index) { throw 'Unknown fixture adapter.' }
    [pscustomobject]@{ Name=$global:LabAdapterFixture.name; InterfaceGuid=$global:LabAdapterFixture.guid }
}

function Get-LabRegistration([int]$Index) {
    [void](Get-LabAdapter $Index)
    $mode = $global:LabAdapterFixture.ipv4.register
    [pscustomobject]@{ RegisterThisConnectionsAddress=($mode -ne 'none'); UseSuffixWhenRegistering=($mode -eq 'both') }
}

function Get-LabServers([int]$Index, [string]$Family) {
    [void](Get-LabAdapter $Index)
    if ($Family -eq 'IPv4') { return $global:LabAdapterFixture.ipv4.servers }
    if ($Family -eq 'IPv6') { return $global:LabAdapterFixture.ipv6 }
    throw 'Unknown fixture address family.'
}

function Test-LabAdministrator { $global:LabAdapterFixture.admin }
function Test-LabProxy { $global:LabAdapterFixture.udp -and $global:LabAdapterFixture.tcp }

function Test-FixtureFailure([string]$Point) {
    $fixture = $global:LabAdapterFixture
    if ($fixture.failures -contains $Point) {
        $fixture.failures = @($fixture.failures | Where-Object { $_ -ne $Point })
        throw "Injected failure $Point"
    }
}

function Invoke-LabNetsh([string[]]$Arguments) {
    $fixture = $global:LabAdapterFixture
    $v4 = $fixture.ipv4
    if (($Arguments -join ' ') -ceq 'interface ipv4 dump') {
        $first = @($v4.servers | Select-Object -First 1)
        $address = if ($first.Count) { $first[0] } else { 'none' }
        'set dnsservers name="Other Ethernet" source=dhcp'
        $line = 'set dnsservers name="{0}" source={1} address={2} register={3}' -f $fixture.name,$v4.source,$address,$v4.register
        $line
        if ($fixture.ambiguous) { $line }
        return
    }
    if ($Arguments.Count -lt 5 -or ($Arguments[0..1] -join ' ') -cne 'interface ipv4' -or
        $Arguments[3] -cne 'dnsservers') { throw 'Unexpected command in fake netsh.' }
    $values = @{}
    foreach ($item in $Arguments[4..($Arguments.Count-1)]) {
        $key, $value = $item -split '=',2
        $values[$key] = $value
    }
    if ($values.name -ne $fixture.index -or $values.validate -cne 'no') { throw 'Wrong fixture adapter or flags.' }
    $fixture.writes = @($fixture.writes) + ($Arguments -join ' ')
    if ($Arguments[2] -ceq 'set') {
        $point = "set:$($values.source):$($values.address)"
        Test-FixtureFailure "before:$point"
        $v4.source, $v4.register = $values.source, $values.register
        if ($values.source -ceq 'dhcp' -and !$values.ContainsKey('address')) {
            $v4.servers = @($fixture.lease)
        } elseif ($values.source -ceq 'static' -and $values.address) {
            $v4.servers = @($values.address | Where-Object { $_ -ne 'none' })
        } else { throw 'Invalid fixture DNS source.' }
        Test-FixtureFailure "after:$point"
        return
    }
    if ($Arguments[2] -ceq 'add') {
        Test-FixtureFailure "before:add:$($values.address)"
        if ($v4.source -ne 'static' -or [int]$values.index -ne $v4.servers.Count + 1) {
            throw 'Invalid index in fixture static DNS server list.'
        }
        $v4.servers = @($v4.servers) + $values.address
        Test-FixtureFailure "after:add:$($values.address)"
        return
    }
    throw 'Unsupported mutation in fake netsh.'
}
